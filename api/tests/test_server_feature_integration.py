"""Verify actual overlaps between deployed health features and reviewed roles."""

from datetime import UTC, datetime
from uuid import uuid4

from antang_api.models import UserRole
from antang_api.patient_access import set_user_role


async def signup(client, **extra):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "unified_" + uuid4().hex[:12],
            "password": "unified-password",
            "full_name": "同名体验患者",
            "consent_accepted": True,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    account = response.json()
    return account, {"Authorization": "Bearer " + account["access_token"]}


async def test_registration_retains_server_consent_and_local_clinician_approval(client):
    payload = {
        "username": "consent_" + uuid4().hex[:12],
        "password": "unified-password",
    }
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 422
    assert (
        await client.post(
            "/api/v1/auth/register", json={**payload, "consent_accepted": False}
        )
    ).status_code == 422
    applicant, _ = await signup(
        client, registration_kind="clinician", organization="测试机构"
    )
    assert applicant["user"]["role"] == "patient"
    assert applicant["user"]["application_status"] == "pending"


async def test_added_patient_health_routes_respect_roles_and_preserve_existing_data(
    client, db_session
):
    patient, headers = await signup(client)
    _, second_headers = await signup(client)
    recorded = await client.post(
        "/api/v1/health-profile/glucose-readings",
        headers=headers,
        json={
            "value_mg_dl": 105,
            "measured_at": datetime.now(UTC).isoformat(),
            "context": "fasting",
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert (
        len(
            (
                await client.get(
                    "/api/v1/health-profile/glucose-readings", headers=headers
                )
            ).json()
        )
        == 1
    )
    assert (
        await client.get(
            "/api/v1/health-profile/glucose-readings", headers=second_headers
        )
    ).json() == []
    for role in (UserRole.CLINICIAN, UserRole.RESEARCHER):
        await set_user_role(
            db_session,
            username=patient["user"]["username"],
            role=role,
            maintainer="test-sync",
        )
        for path in ("glucose-readings", "fear-summary", "fear-behavior"):
            assert (
                await client.get("/api/v1/health-profile/" + path, headers=headers)
            ).status_code == 403
        assert (
            await client.post(
                "/api/v1/voice/transcribe",
                headers=headers,
                files={"file": ("voice.wav", b"test-only", "audio/wav")},
            )
        ).status_code == 403


async def test_server_account_deletion_remains_available_with_new_roles(client):
    account, headers = await signup(client)
    response = await client.request(
        "DELETE",
        "/api/v1/auth/account",
        headers=headers,
        json={"password": "unified-password"},
    )
    assert response.status_code == 204, response.text
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={
                "username": account["user"]["username"],
                "password": "unified-password",
            },
        )
    ).status_code == 401


async def test_deployed_instrument_remains_available_only_to_patients(client, db_session):
    path = "/api/v1/health-profile/psychosocial-instrument"
    assert (await client.get(path)).status_code == 401
    patient, headers = await signup(client)
    response = await client.get(path, headers=headers)
    assert response.status_code == 200
    instrument = response.json()
    assert instrument["id"] == "antang-behavior-v1"
    assert len(instrument["questions"]) == 6
    assert {q["kind"] for q in instrument["questions"]} == {"behavior", "worry"}
    assert [option["value"] for option in instrument["options"]] == list(range(5))
    assert [option["value"] for option in instrument["gold_options"]] == list(range(1, 8))
    assert "不是标准化量表" in instrument["disclaimer"]
    for role in (UserRole.CLINICIAN, UserRole.RESEARCHER):
        await set_user_role(db_session, username=patient["user"]["username"], role=role, maintainer="test-sync")
        assert (await client.get(path, headers=headers)).status_code == 403


async def test_a_saved_report_summary_is_readable_only_by_its_patient(
    client, db_session
):
    """报告速览是患者自己的数据：别人看不到。"""

    from datetime import date

    from sqlalchemy import select

    from antang_api.health_profile.glucose import record_glucose_report
    from antang_api.models import User

    path = "/api/v1/health-profile/glucose-reports"
    assert (await client.get(path)).status_code == 401

    patient, headers = await signup(client)
    _, second_headers = await signup(client)
    user = (
        await db_session.execute(
            select(User).where(User.username == patient["user"]["username"])
        )
    ).scalar_one()
    await record_glucose_report(
        db_session,
        user_id=user.id,
        period_start=date(2024, 5, 30),
        period_end=date(2024, 6, 11),
        device="硅基动感Pro",
        mean_mmol=6.29,
        tir_percent=84.5,
    )
    await db_session.commit()

    mine = await client.get(path, headers=headers)
    assert mine.status_code == 200, mine.text
    items = mine.json()["items"]
    assert len(items) == 1
    assert items[0]["period_start"] == "2024-05-30"
    assert float(items[0]["tir_percent"]) == 84.5
    assert items[0]["note"] is None

    assert (await client.get(path, headers=second_headers)).json()["items"] == []


async def test_a_patient_can_delete_a_report_summary_but_not_someone_elses(
    client, db_session
):
    """删报告速览是真删：自己删得掉，别人的那条连「存在」都不该暴露。"""

    from datetime import date

    from sqlalchemy import select

    from antang_api.health_profile.glucose import record_glucose_report
    from antang_api.models import User

    path = "/api/v1/health-profile/glucose-reports"
    patient, headers = await signup(client)
    _, second_headers = await signup(client)
    user = (
        await db_session.execute(
            select(User).where(User.username == patient["user"]["username"])
        )
    ).scalar_one()
    report = await record_glucose_report(
        db_session,
        user_id=user.id,
        period_start=date(2025, 7, 28),
        period_end=date(2025, 7, 31),
        device="硅基动感Pro",
        lbgi_level="high",
    )
    await db_session.commit()
    report_id = str(report.id)

    assert (await client.delete(f"{path}/{report_id}")).status_code == 401
    # 别人的报告：404，而不是 403——别让「这份存在」变成可探测的信息。
    assert (
        await client.delete(f"{path}/{report_id}", headers=second_headers)
    ).status_code == 404
    assert len((await client.get(path, headers=headers)).json()["items"]) == 1

    removed = await client.delete(f"{path}/{report_id}", headers=headers)
    assert removed.status_code == 204, removed.text
    assert (await client.get(path, headers=headers)).json()["items"] == []
    # 删第二次就是没找到，不是静默成功。
    assert (
        await client.delete(f"{path}/{report_id}", headers=headers)
    ).status_code == 404


async def test_a_patient_sets_their_own_glucose_target_range(client):
    """目标范围是患者的设置：改完统计和界面都按他的算，别人的不受影响。"""

    target = "/api/v1/health-profile/glucose-target"
    assert (await client.get(target)).status_code == 401

    _, headers = await signup(client)
    _, second_headers = await signup(client)

    default = await client.get(target, headers=headers)
    assert default.status_code == 200, default.text
    assert default.json() == {"low_mg_dl": 70, "high_mg_dl": 180}

    changed = await client.put(
        target, headers=headers, json={"low_mg_dl": 80, "high_mg_dl": 160}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {"low_mg_dl": 80, "high_mg_dl": 160}

    summary = await client.get(
        "/api/v1/health-profile/glucose-readings/summary", headers=headers
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["target_low_mg_dl"] == 80
    assert summary.json()["target_high_mg_dl"] == 160

    # 别人的范围没被改。
    other = await client.get(target, headers=second_headers)
    assert other.json() == {"low_mg_dl": 70, "high_mg_dl": 180}

    # 下限不小于上限是错，不是悄悄修好。
    assert (
        await client.put(
            target, headers=headers, json={"low_mg_dl": 200, "high_mg_dl": 100}
        )
    ).status_code == 422

