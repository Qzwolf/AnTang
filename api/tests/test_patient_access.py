"""使用真实认证和隔离 PostgreSQL 验证患者授权闭环。"""

from uuid import UUID, uuid4
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import PatientAccessAudit, PersonalProfile, User, UserRole
from antang_api.patient_access import set_user_role


async def account(
    client: AsyncClient, session: AsyncSession, role: UserRole = UserRole.PATIENT
):
    name = f"access_{uuid4().hex[:12]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={"consent_accepted": True, "username": name, "password": "patient-access-test"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["role"] == "patient"
    if role != UserRole.PATIENT:
        await set_user_role(
            session, username=name, role=role, maintainer="test-maintainer"
        )
    return data["user"], {"Authorization": f"Bearer {data['access_token']}"}


async def grant(client: AsyncClient, headers: dict, clinician_id: str):
    return await client.post(
        "/api/v1/patient-access-grants",
        headers=headers,
        json={"clinician_id": clinician_id},
    )


async def test_grant_read_revoke_with_original_session(
    client: AsyncClient, db_session: AsyncSession
):
    patient, patient_headers = await account(client, db_session)
    other_patient, other_headers = await account(client, db_session)
    clinician, clinician_headers = await account(client, db_session, UserRole.CLINICIAN)
    root = f"/api/v1/patients/{patient['id']}"
    personal = await db_session.get(PersonalProfile, UUID(patient["id"]))
    assert personal is not None
    personal.resident_area = "患者甲的档案"
    await db_session.commit()
    end = datetime.now(UTC) - timedelta(minutes=1)
    imported = await client.post(
        "/api/v1/health-profile/wearable-imports",
        headers=patient_headers,
        json={
            "client_sync_id": str(uuid4()),
            "record_type": "steps",
            "records": [
                {
                    "record_type": "steps",
                    "external_record_id": "patient-a-steps",
                    "start_time": (end - timedelta(minutes=10)).isoformat(),
                    "end_time": end.isoformat(),
                    "source_package": "com.huami.watch.hmwatchmanager",
                    "recording_method": 2,
                    "source_last_modified_at": end.isoformat(),
                    "data": {"count": 321},
                }
            ],
        },
    )
    assert imported.status_code == 201
    assert (
        await client.get(root + "/health-profile", headers=clinician_headers)
    ).status_code == 403
    assert (await client.get("/api/v1/patients", headers=clinician_headers)).json()[
        "patients"
    ] == []
    resolved = await client.post(
        "/api/v1/patient-access-grants/resolve",
        headers=patient_headers,
        json={"username": clinician["username"].upper()},
    )
    assert resolved.json()["id"] == clinician["id"]
    created = await grant(client, patient_headers, clinician["id"])
    assert created.status_code == 201
    grant_id = created.json()["id"]
    assert (await grant(client, patient_headers, clinician["id"])).status_code == 409
    patients = (await client.get("/api/v1/patients", headers=clinician_headers)).json()
    assert [item["id"] for item in patients["patients"]] == [patient["id"]]
    for resource in ("health-profile", "wearable-dashboard"):
        response = await client.get(root + "/" + resource, headers=clinician_headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        if resource == "health-profile":
            assert (
                response.json()["personal_profile"]["resident_area"] == "患者甲的档案"
            )
            assert response.json()["wearable_latest"][0]["data"]["count"] == 321
        else:
            assert response.json()["sources"][0]["totals"]["steps"] == 321
        assert (
            await client.get(
                f"/api/v1/patients/{other_patient['id']}/{resource}",
                headers=clinician_headers,
            )
        ).status_code == 403
    assert (
        await client.get(root + "/health-profile", headers=other_headers)
    ).status_code == 403
    # 医护没有跨患者写接口，患者也不能撤销别人的授权。
    assert (
        await client.post(root + "/health-profile", headers=clinician_headers, json={})
    ).status_code == 405
    assert (
        await client.delete(
            f"/api/v1/patient-access-grants/{grant_id}", headers=other_headers
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/v1/patient-access-grants/{grant_id}", headers=patient_headers
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/v1/patient-access-grants/{grant_id}", headers=patient_headers
        )
    ).status_code == 204
    for resource in ("health-profile", "wearable-dashboard"):
        assert (
            await client.get(root + "/" + resource, headers=clinician_headers)
        ).status_code == 403
    assert (await client.get("/api/v1/patients", headers=clinician_headers)).json()[
        "patients"
    ] == []
    history = (
        await client.get("/api/v1/patient-access-grants", headers=patient_headers)
    ).json()["grants"]
    assert history[0]["revoked_at"] is not None
    assert not history[0]["available"]
    again = await grant(client, patient_headers, clinician["id"])
    assert again.status_code == 201 and again.json()["id"] != grant_id
    assert (
        await client.get(root + "/health-profile", headers=clinician_headers)
    ).status_code == 200
    actions = set(await db_session.scalars(select(PatientAccessAudit.action)))
    assert {
        "grant",
        "revoke",
        "health_profile:allowed",
        "health_profile:denied",
        "wearable_dashboard:denied",
    } <= actions


@pytest.mark.parametrize("role", ["clinician", "researcher"])
async def test_registration_cannot_choose_role(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": f"promote_{uuid4().hex[:8]}",
            "password": "patient-access-test",
            "role": role,
        },
    )
    assert response.status_code == 422


async def test_researcher_and_patient_cannot_enumerate_or_read_patients(
    client: AsyncClient, db_session: AsyncSession
):
    patient, patient_headers = await account(client, db_session)
    researcher, researcher_headers = await account(
        client, db_session, UserRole.RESEARCHER
    )
    for headers in (patient_headers, researcher_headers):
        assert (
            await client.get("/api/v1/patients", headers=headers)
        ).status_code == 403
        for resource in ("health-profile", "wearable-dashboard"):
            assert (
                await client.get(
                    f"/api/v1/patients/{patient['id']}/{resource}", headers=headers
                )
            ).status_code == 403
    assert (
        await client.get("/api/v1/health-profile", headers=researcher_headers)
    ).status_code == 403
    assert (await grant(client, patient_headers, researcher["id"])).status_code == 404
    assert (await grant(client, researcher_headers, patient["id"])).status_code == 403
    assert (await client.get("/api/v1/auth/me", headers=researcher_headers)).json()[
        "role"
    ] == "researcher"


async def test_role_change_and_disabled_accounts_take_effect_on_existing_session(
    client: AsyncClient, db_session: AsyncSession
):
    patient, ph = await account(client, db_session)
    clinician, ch = await account(client, db_session, UserRole.CLINICIAN)
    root = f"/api/v1/patients/{patient['id']}/health-profile"
    assert (await grant(client, ph, clinician["id"])).status_code == 201
    await set_user_role(
        db_session,
        username=clinician["username"],
        role=UserRole.RESEARCHER,
        maintainer="test-maintainer",
    )
    assert (await client.get(root, headers=ch)).status_code == 403
    await set_user_role(
        db_session,
        username=clinician["username"],
        role=UserRole.CLINICIAN,
        maintainer="test-maintainer",
    )
    assert (await client.get(root, headers=ch)).status_code == 403
    assert (await grant(client, ph, clinician["id"])).status_code == 201
    stored_patient = await db_session.get(User, UUID(patient["id"]))
    assert stored_patient is not None
    stored_patient.is_active = False
    await db_session.commit()
    assert (await client.get(root, headers=ch)).status_code == 403
    assert (await client.get("/api/v1/patients", headers=ch)).json()["patients"] == []
    stored_patient.is_active = True
    stored_clinician = await db_session.get(User, UUID(clinician["id"]))
    assert stored_clinician is not None
    stored_clinician.is_active = False
    await db_session.commit()
    assert (await client.get(root, headers=ch)).status_code == 401


async def test_only_the_authorized_clinician_sees_the_scale_scores(
    client: AsyncClient, db_session: AsyncSession
):
    """回避分只给医生：患者自己提交后拿不到分数，授权医护才看得到。"""

    patient, ph = await account(client, db_session)
    clinician, ch = await account(client, db_session, UserRole.CLINICIAN)
    stranger, sh = await account(client, db_session, UserRole.CLINICIAN)
    root = f"/api/v1/patients/{patient['id']}/psychosocial-assessment"
    assert (await grant(client, ph, clinician["id"])).status_code == 201

    recorded = await client.post(
        "/api/v1/health-profile/psychosocial-assessment",
        headers=ph,
        json={
            "fear_behavior_percent": 40,
            "fear_worry_percent": 60,
            "gold_score": 5,
            "assessed_at": datetime.now(UTC).isoformat(),
        },
    )
    assert recorded.status_code == 201
    assert "fear_percent" not in recorded.json()
    assert "quadrant" not in recorded.json()

    body = (await client.get(root, headers=ch)).json()
    assert body["fear_behavior_percent"] == 40
    assert body["fear_worry_percent"] == 60
    assert body["fear_percent"] == 50
    assert body["quadrant"] == "high_fear_impaired_awareness"
    assert body["weekly_contact_limit"] == 4

    assert (await client.get(root, headers=sh)).status_code == 403
    assert (await client.get(root)).status_code == 401


async def test_patient_list_paginates_only_authorized_patients(
    client: AsyncClient, db_session: AsyncSession
):
    clinician, ch = await account(client, db_session, UserRole.CLINICIAN)
    ids = []
    for _ in range(2):
        patient, ph = await account(client, db_session)
        ids.append(patient["id"])
        assert (await grant(client, ph, clinician["id"])).status_code == 201
    await account(client, db_session)  # 未授权患者不应出现在任何一页。
    first = (await client.get("/api/v1/patients?limit=1", headers=ch)).json()
    second = (
        await client.get(
            f"/api/v1/patients?limit=1&after={first['next_after']}", headers=ch
        )
    ).json()
    assert {first["patients"][0]["id"], second["patients"][0]["id"]} == set(ids)
    assert second["next_after"] is None
