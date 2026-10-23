from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import render_core_system_prompt
from antang_api.database import get_session
from antang_api.models import PsychosocialAssessment, User
from antang_api.psychosocial import (
    AWARENESS_IMPAIRED_GOLD,
    FEAR_HIGH_PERCENT,
    PsychosocialProfile,
    read_latest_assessment,
    render_guidance,
    score_answers,
)
from antang_api.routers.auth import router as auth_router
from antang_api.routers.health_profile import router as health_profile_router


async def _client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(health_profile_router)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def _register(client: AsyncClient) -> tuple[dict[str, str], str]:
    username = f"psy_{uuid4().hex[:16]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "correct-password",
            "consent_accepted": True,
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, username


def test_quadrant_follows_fear_and_awareness() -> None:
    cases = [
        (80, 80, 2, "high_fear_normal_awareness"),
        (80, 80, 6, "high_fear_impaired_awareness"),
        (10, 20, 1, "low_fear_normal_awareness"),
        (10, 20, 7, "low_fear_impaired_awareness"),
        (80, 80, None, "unknown_awareness"),
        (10, 20, None, "unknown_awareness"),
    ]
    for behavior, worry, gold, expected in cases:
        profile = PsychosocialProfile(
            fear_behavior_percent=behavior,
            fear_worry_percent=worry,
            gold_score=gold,
        )
        assert profile.quadrant == expected


def test_thresholds_sit_exactly_where_the_boundaries_are_documented() -> None:
    at_threshold = PsychosocialProfile(
        fear_behavior_percent=FEAR_HIGH_PERCENT,
        fear_worry_percent=FEAR_HIGH_PERCENT,
        gold_score=AWARENESS_IMPAIRED_GOLD,
    )
    assert at_threshold.fear_high is True
    assert at_threshold.awareness == "impaired"

    just_below = PsychosocialProfile(
        fear_behavior_percent=FEAR_HIGH_PERCENT - 1,
        fear_worry_percent=FEAR_HIGH_PERCENT - 1,
        gold_score=AWARENESS_IMPAIRED_GOLD - 1,
    )
    assert just_below.fear_high is False
    assert just_below.awareness == "normal"


def test_fear_percent_averages_the_two_subscales() -> None:
    profile = PsychosocialProfile(
        fear_behavior_percent=40,
        fear_worry_percent=60,
        gold_score=None,
    )
    assert profile.fear_percent == 50


def test_profile_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValueError):
        PsychosocialProfile(
            fear_behavior_percent=101,
            fear_worry_percent=50,
            gold_score=3,
        )
    with pytest.raises(ValueError):
        PsychosocialProfile(
            fear_behavior_percent=50,
            fear_worry_percent=50,
            gold_score=8,
        )


def test_guidance_states_the_contact_limit_without_quoting_scores() -> None:
    profile = PsychosocialProfile(
        fear_behavior_percent=90,
        fear_worry_percent=90,
        gold_score=6,
    )
    guidance = render_guidance(profile)
    assert "[沟通方式参考]" in guidance
    assert f"不超过 {profile.weekly_contact_limit} 次" in guidance
    # 分数是内部参考，不能出现在要交给模型的原话里。
    assert "90" not in guidance
    assert "HFS" not in guidance
    assert "Gold" not in guidance


def test_guidance_is_empty_without_an_assessment() -> None:
    assert render_guidance(None) == ""


def test_prompt_appends_guidance_after_the_profile_block() -> None:
    guidance = render_guidance(
        PsychosocialProfile(
            fear_behavior_percent=10,
            fear_worry_percent=10,
            gold_score=1,
        )
    )
    prompt = render_core_system_prompt(
        "档案内容",
        current_time_context="2026-11-02 10:00",
        psychosocial_context=guidance,
    )
    assert prompt.index("[当前健康档案]") < prompt.index("[沟通方式参考]")

    without = render_core_system_prompt(
        "档案内容",
        current_time_context="2026-11-02 10:00",
    )
    assert "[沟通方式参考]" not in without


async def test_assessment_round_trip(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        headers, _ = await _register(client)

        empty = await client.get(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
        )
        assert empty.status_code == 200
        assert empty.json() is None

        assessed_at = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
        created = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 40,
                "fear_worry_percent": 60,
                "gold_score": 5,
                "instrument": "HFS-II",
                "assessed_at": assessed_at.isoformat(),
            },
        )
        assert created.status_code == 201
        body = created.json()
        # 患者端不返回任何分数：分数只走医护端（/patients/{id}/psychosocial-assessment）。
        assert "fear_percent" not in body
        assert "fear_high" not in body
        assert "quadrant" not in body
        assert "awareness" not in body
        assert body["weekly_contact_limit"] == 4
        assert datetime.fromisoformat(body["assessed_at"]) == assessed_at

        latest = await client.get(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
        )
        assert latest.status_code == 200
        assert latest.json()["weekly_contact_limit"] == 4
        assert "fear_percent" not in latest.json()


async def test_the_server_scores_the_answers_it_asked_for(db_session: AsyncSession) -> None:
    """App 只上传逐题选择，百分比由服务端算：换量表连计分都不用改客户端。"""

    async for client in _client(db_session):
        headers, _ = await _register(client)
        assessed_at = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
        created = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "answers": [
                    {"id": "repeat-test", "value": 4},
                    {"id": "skip-exercise", "value": 2},
                    {"id": "bedtime-snack", "value": 0},
                    {"id": "night-worry", "value": 3},
                    {"id": "alone-worry", "value": 1},
                    {"id": "anxious-thought", "value": 0},
                ],
                "gold_score": 3,
                "instrument": "antang-behavior-v1",
                "assessed_at": assessed_at.isoformat(),
            },
        )
        assert created.status_code == 201

        row = await db_session.scalar(
            select(PsychosocialAssessment).where(
                PsychosocialAssessment.assessed_at == assessed_at
            )
        )
        assert row is not None
        # 行为 (4+2+0)/3/4 = 50%；担忧 (3+1+0)/3/4 = 33%。
        assert row.fear_behavior_percent == 50
        assert row.fear_worry_percent == 33


async def test_partial_or_foreign_answers_are_refused(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        headers, _ = await _register(client)
        path = "/api/v1/health-profile/psychosocial-assessment"
        base = {
            "gold_score": 3,
            "assessed_at": datetime(2026, 10, 31, 9, 0, tzinfo=UTC).isoformat(),
        }
        complete = [
            {"id": "repeat-test", "value": 1},
            {"id": "skip-exercise", "value": 1},
            {"id": "bedtime-snack", "value": 1},
            {"id": "night-worry", "value": 1},
            {"id": "alone-worry", "value": 1},
            {"id": "anxious-thought", "value": 1},
        ]

        missing = await client.post(
            path, headers=headers, json={**base, "answers": complete[:-1]}
        )
        assert missing.status_code == 422
        assert "还有题没答" in missing.json()["detail"]

        unknown = await client.post(
            path,
            headers=headers,
            json={
                **base,
                "answers": [*complete, {"id": "not-in-instrument", "value": 1}],
            },
        )
        assert unknown.status_code == 422
        assert "没有这道题" in unknown.json()["detail"]

        duplicate = await client.post(
            path, headers=headers, json={**base, "answers": [*complete, complete[0]]}
        )
        assert duplicate.status_code == 422

        out_of_range = await client.post(
            path,
            headers=headers,
            json={
                **base,
                "answers": [{**complete[0], "value": 9}, *complete[1:]],
            },
        )
        assert out_of_range.status_code == 422

        neither = await client.post(path, headers=headers, json=base)
        assert neither.status_code == 422


async def test_latest_assessment_wins_by_assessed_at(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        headers, _ = await _register(client)
        base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

        for offset, gold in ((0, 1), (1, 7)):
            response = await client.post(
                "/api/v1/health-profile/psychosocial-assessment",
                headers=headers,
                json={
                    "fear_behavior_percent": 20,
                    "fear_worry_percent": 20,
                    "gold_score": gold,
                    "assessed_at": (base + timedelta(days=offset)).isoformat(),
                },
            )
            assert response.status_code == 201

        latest = await client.get(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
        )
        assert latest.json()["weekly_contact_limit"] == 5
        assert "quadrant" not in latest.json()


async def test_chat_reads_the_latest_assessment_of_that_user(
    db_session: AsyncSession,
) -> None:
    username = ""
    async for client in _client(db_session):
        headers, username = await _register(client)
        response = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 70,
                "fear_worry_percent": 70,
                "gold_score": 6,
                "assessed_at": datetime(2026, 10, 31, 9, 0, tzinfo=UTC).isoformat(),
            },
        )
        assert response.status_code == 201

    assert username, "注册没有执行"
    user = await db_session.scalar(select(User).where(User.username == username))
    assert user is not None
    user_id: UUID = user.id

    profile = await read_latest_assessment(db_session, user_id)
    assert profile is not None
    assert profile.quadrant == "high_fear_impaired_awareness"
    assert profile.weekly_contact_limit == 4


async def test_assessment_rejects_naive_time_and_unknown_fields(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers, _ = await _register(client)
        naive = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 40,
                "fear_worry_percent": 40,
                "assessed_at": "2026-10-31T09:00:00",
            },
        )
        assert naive.status_code == 422

        extra = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 40,
                "fear_worry_percent": 40,
                "assessed_at": "2026-10-31T09:00:00+00:00",
                "diagnosis": "T1D",
            },
        )
        assert extra.status_code == 422

        out_of_range = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 140,
                "fear_worry_percent": 40,
                "assessed_at": "2026-10-31T09:00:00+00:00",
            },
        )
        assert out_of_range.status_code == 422


async def test_the_app_asks_the_server_what_to_ask(db_session: AsyncSession) -> None:
    """题目由服务端下发，换量表不用重打安装包。"""

    async for client in _client(db_session):
        anonymous = await client.get("/api/v1/health-profile/psychosocial-instrument")
        assert anonymous.status_code == 401

        headers, _ = await _register(client)
        response = await client.get(
            "/api/v1/health-profile/psychosocial-instrument",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "antang-behavior-v1"
        assert body["title"] == "沟通方式自评"
        assert "六个问题" in body["instructions"]
        assert "不是标准化量表" in body["disclaimer"]

        options = body["options"]
        assert [option["value"] for option in options] == [0, 1, 2, 3, 4]
        assert [option["label"] for option in options] == [
            "从不",
            "很少",
            "有时",
            "经常",
            "几乎每天",
        ]
        assert [option["value"] for option in body["gold_options"]] == list(range(1, 8))

        questions = body["questions"]
        assert len(questions) == 6
        assert [question["kind"] for question in questions] == [
            "behavior",
            "behavior",
            "behavior",
            "worry",
            "worry",
            "worry",
        ]
        assert len({question["id"] for question in questions}) == len(questions)
        assert all(question["text"].startswith("过去一个月") for question in questions)
        assert body["gold_hint"] == "1 = 总能感觉到，7 = 完全感觉不到"

        # App 拿到的 id 原样回传就能存进库：两边不能各写一份字符串。
        created = await client.post(
            "/api/v1/health-profile/psychosocial-assessment",
            headers=headers,
            json={
                "fear_behavior_percent": 100,
                "fear_worry_percent": 0,
                "gold_score": 2,
                "instrument": body["id"],
                "assessed_at": "2026-10-31T09:00:00+00:00",
            },
        )
        assert created.status_code == 201
        assert created.json()["instrument"] == body["id"]

def test_score_answers_refuses_incomplete_or_foreign_choices() -> None:
    """缺题、越界都要报错：分档会影响联系频率，不能存一个算错的分。"""

    with pytest.raises(ValueError, match="没有这道题"):
        score_answers({"not-in-instrument": 1})
    with pytest.raises(ValueError, match="还有题没答"):
        score_answers({"repeat-test": 1})
    with pytest.raises(ValueError, match="不在选项里"):
        score_answers(
            {
                "repeat-test": 9,
                "skip-exercise": 1,
                "bedtime-snack": 1,
                "night-worry": 1,
                "alone-worry": 1,
                "anxious-thought": 1,
            }
        )
