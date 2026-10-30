from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.behavioral import record_behavior, record_belief
from antang_api.database import get_session
from antang_api.models import (
    GlucoseReading,
    MealLog,
    MedicationLog,
    Message,
    MessageRole,
    MessageStatus,
    User,
    UserRole,
)
from antang_api.patient_access import set_user_role
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


async def _register(client: AsyncClient) -> tuple[dict, dict[str, str]]:
    username = f"exp_{uuid4().hex[:12]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={"consent_accepted": True, "username": username, "password": "correct-password"},
    )
    assert response.status_code == 201
    body = response.json()
    return body["user"], {"Authorization": f"Bearer {body['access_token']}"}


async def test_export_returns_the_patients_own_records(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        registered, headers = await _register(client)
        user = await db_session.get(User, registered["id"])
        assert user is not None

        now = datetime.now(UTC)
        db_session.add(
            GlucoseReading(
                user_id=user.id,
                value_mg_dl=86,
                measured_at=now - timedelta(hours=2),
                context="fasting",
                source="fingerstick",
                note="早上空腹",
            )
        )
        db_session.add(
            GlucoseReading(
                user_id=user.id,
                value_mg_dl=70,
                measured_at=now - timedelta(hours=1),
                context="other",
                revoked_at=now,
            )
        )
        db_session.add(
            MealLog(
                user_id=user.id,
                eaten_at=now - timedelta(hours=3),
                meal_type="lunch",
                description="一碗米饭",
                carbs_grams=45,
            )
        )
        db_session.add(
            MedicationLog(
                user_id=user.id,
                taken_at=now - timedelta(hours=4),
                name="诺和锐",
                dose_text="6 单位",
                kind="insulin",
            )
        )
        db_session.add(
            Message(
                user_id=user.id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="我今天中午吃了一碗米饭",
                client_message_id=None,
            )
        )
        await db_session.commit()

        record_behavior(
            db_session, user_id=user.id, kind="night_check", count=3, note="凌晨起来查了三次"
        )
        record_belief(
            db_session,
            user_id=user.id,
            belief_code="hypo_causes_death",
            quote="低血糖就是会死",
        )
        await db_session.commit()

        response = await client.get("/api/v1/health-profile/export", headers=headers)
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store"

        payload = response.json()
        assert payload["format"] == "antang-export-v1"
        assert payload["account"]["username"] == user.username
        # 撤回的那条不该出现在导出里，患者删掉的就是删掉了。
        assert [row["value_mg_dl"] for row in payload["glucose_readings"]] == [86]
        assert payload["glucose_readings"][0]["note"] == "早上空腹"
        assert [row["description"] for row in payload["meals"]] == ["一碗米饭"]
        assert payload["meals"][0]["carbs_grams"] == 45
        assert payload["medications"][0]["dose_text"] == "6 单位"
        assert payload["fear_behavior_logs"][0]["kind"] == "night_check"
        assert payload["belief_events"][0]["belief_code"] == "hypo_causes_death"
        assert payload["counts"]["glucose_readings"] == 1
        assert payload["counts"]["messages"] >= 1
        assert payload["conversationless_messages"], "会话层之前的老消息不能丢"


async def test_export_is_not_available_to_other_roles(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        registered, headers = await _register(client)
        await set_user_role(
            db_session,
            username=registered["username"],
            role=UserRole.CLINICIAN,
            maintainer="test-maintainer",
        )

        anonymous = await client.get("/api/v1/health-profile/export")
        assert anonymous.status_code == 401

        forbidden = await client.get("/api/v1/health-profile/export", headers=headers)
        assert forbidden.status_code == 403
