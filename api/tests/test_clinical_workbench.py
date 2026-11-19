"""Use real authentication and isolated PostgreSQL for the clinical read boundary."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from antang_api.models import PatientAccessAudit, UserRole
from antang_api.models.clinical_access import ClinicalAccessSession
from antang_api.patient_access import set_user_role

PASSWORD = "clinical-account-password"
SHARED = "shared-clinical-password"


async def account(client, session, role=UserRole.PATIENT):
    name = "clinical_" + uuid4().hex[:12]
    response = await client.post("/api/v1/auth/register", json={"username": name, "password": PASSWORD, "full_name": "工作台测试患者", "consent_accepted": True})
    assert response.status_code == 201
    data = response.json()
    if role != UserRole.PATIENT:
        await set_user_role(session, username=name, role=role, maintainer="test-clinical")
    return data, {"Authorization": "Bearer " + data["access_token"]}


async def configure(client, admin, password=SHARED):
    response = await client.put("/api/v1/admin/clinical-access-password", headers=admin, json={"account_password": PASSWORD, "new_password": password})
    assert response.status_code == 204, response.text


async def unlock(client, clinician, password=SHARED):
    response = await client.post("/api/v1/clinical/access/unlock", headers=clinician, json={"password": password})
    assert response.status_code == 200, response.text
    return {**clinician, "X-Clinical-Unlock": response.json()["token"]}


async def test_granted_timeline_uses_real_window_and_revocation(client, db_session):
    patient, patient_headers = await account(client, db_session)
    other, other_headers = await account(client, db_session)
    doctor, doctor_headers = await account(client, db_session, UserRole.CLINICIAN)
    root = "/api/v1/clinical/patients/" + patient["user"]["id"] + "/overview"
    assert (await client.get(root, headers=doctor_headers)).status_code == 403
    grant = await client.post("/api/v1/patient-access-grants", headers=patient_headers, json={"clinician_id": doctor["user"]["id"]})
    assert grant.status_code == 201
    now = datetime.now(UTC)
    for value, day in ((90, 2), (150, 1), (300, 40), (400, 1)):
        reading = await client.post("/api/v1/health-profile/glucose-readings", headers=patient_headers, json={"value_mg_dl": value, "measured_at": (now - timedelta(days=day)).isoformat()})
        assert reading.status_code == 201
        if value == 400:
            assert (await client.delete("/api/v1/health-profile/glucose-readings/" + reading.json()["id"], headers=patient_headers)).status_code == 204
    await client.post("/api/v1/health-profile/glucose-readings", headers=other_headers, json={"value_mg_dl": 500, "measured_at": now.isoformat()})
    response = await client.get(root, headers=doctor_headers, params={"start": (now - timedelta(days=7)).isoformat(), "end": (now + timedelta(seconds=1)).isoformat()})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert [p["value_mg_dl"] for p in data["glucose"]["points"]] == [90, 150]
    assert data["glucose"]["summary"] == {"count": 2, "average_mg_dl": 120.0, "minimum_mg_dl": 90, "maximum_mg_dl": 150}
    newest = data["glucose"]["points"][-1]
    records_path = root.removesuffix("/overview") + "/glucose-records"
    cursor = {"start": data["period_start"], "end": data["period_end"],
              "before_time": newest["measured_at"], "before_id": newest["id"]}
    page = await client.get(records_path, headers=doctor_headers, params=cursor)
    assert page.status_code == 200
    assert [p["value_mg_dl"] for p in page.json()["points"]] == [90]
    assert page.json()["has_more"] is False
    assert data["wearable"]["period_start"] == data["period_start"]
    assert data["wearable"]["period_end"] == data["period_end"]
    assert data["wearable"]["sources"] == []
    assert (await client.get("/api/v1/clinical/patients/" + other["user"]["id"] + "/overview", headers=doctor_headers)).status_code == 403
    await client.delete("/api/v1/patient-access-grants/" + grant.json()["id"], headers=patient_headers)
    assert (await client.get(records_path, headers=doctor_headers, params=cursor)).status_code == 403
    assert (await client.get(root, headers=doctor_headers)).status_code == 403
    assert (await client.get("/api/v1/clinical/patients", headers=doctor_headers)).json()["total"] == 0


async def test_shared_password_scope_is_personal_session_bound_and_revocable(client, db_session):
    patient, _ = await account(client, db_session)
    doctor, headers = await account(client, db_session, UserRole.CLINICIAN)
    _, admin = await account(client, db_session, UserRole.RESEARCHER)
    await configure(client, admin)
    root = "/api/v1/clinical/patients/" + patient["user"]["id"] + "/overview?scope=all"
    assert (await client.get(root, headers=headers)).status_code == 403
    opened = await unlock(client, headers)
    assert (await client.get(root, headers=opened)).status_code == 200
    page = await client.get("/api/v1/clinical/patients?scope=all", headers=opened)
    assert patient["user"]["id"] in [p["id"] for p in page.json()["patients"]]
    assert (await client.get("/api/v1/clinical/patients?scope=granted", headers=opened)).json()["total"] == 0
    # Knowing someone else's secondary token cannot expand a different login session.
    second_login = (await client.post("/api/v1/auth/login", json={"username": doctor["user"]["username"], "password": PASSWORD})).json()
    borrowed = {"Authorization": "Bearer " + second_login["access_token"], "X-Clinical-Unlock": opened["X-Clinical-Unlock"]}
    assert (await client.get(root, headers=borrowed)).status_code == 403
    await configure(client, admin, "rotated-clinical-password")
    assert (await client.get(root, headers=opened)).status_code == 403
    assert (await client.post("/api/v1/clinical/access/unlock", headers=headers, json={"password": SHARED})).status_code == 403
    opened = await unlock(client, headers, "rotated-clinical-password")
    assert (await client.get(root, headers=opened)).status_code == 200
    assert (await client.post("/api/v1/clinical/access/lock", headers=opened)).status_code == 204
    assert (await client.get(root, headers=opened)).status_code == 403
    audits = list(await db_session.scalars(select(PatientAccessAudit).where(PatientAccessAudit.actor_user_id == doctor["user"]["id"], PatientAccessAudit.patient_id == patient["user"]["id"], PatientAccessAudit.action == "clinical:all:overview")))
    assert len(audits) >= 1


async def test_patient_researcher_cannot_use_clinician_unlock_and_admin_changes_need_personal_password(client, db_session):
    _, patient = await account(client, db_session)
    _, admin = await account(client, db_session, UserRole.RESEARCHER)
    await configure(client, admin)
    for headers in (patient, admin):
        assert (await client.post("/api/v1/clinical/access/unlock", headers=headers, json={"password": SHARED})).status_code == 403
        assert (await client.get("/api/v1/clinical/patients?scope=all", headers=headers)).status_code == 403
    assert (await client.put("/api/v1/admin/clinical-access-password", headers=patient, json={"account_password": PASSWORD, "new_password": SHARED})).status_code == 403
    assert (await client.put("/api/v1/admin/clinical-access-password", headers=admin, json={"account_password": "wrong-password", "new_password": SHARED})).status_code == 403


async def test_unlock_expiry_role_change_and_rate_limit(client, db_session):
    doctor, headers = await account(client, db_session, UserRole.CLINICIAN)
    _, admin = await account(client, db_session, UserRole.RESEARCHER)
    await configure(client, admin)
    opened = await unlock(client, headers)
    access = await db_session.scalar(select(ClinicalAccessSession).where(ClinicalAccessSession.user_id == doctor["user"]["id"]))
    assert access is not None
    assert access.expires_at is None
    assert (await client.get("/api/v1/clinical/patients?scope=all", headers=opened)).status_code == 200
    access.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get("/api/v1/clinical/patients?scope=all", headers=opened)).status_code == 403
    for _ in range(5):
        assert (await client.post("/api/v1/clinical/access/unlock", headers=headers, json={"password": "wrong-password"})).status_code == 403
    assert (await client.post("/api/v1/clinical/access/unlock", headers=headers, json={"password": SHARED})).status_code == 429
    await set_user_role(db_session, username=doctor["user"]["username"], role=UserRole.RESEARCHER, maintainer="test-clinical")
    assert (await client.get("/api/v1/clinical/patients", headers=headers)).status_code == 403


async def test_web_entry_and_invalid_time_windows(client, db_session):
    response = await client.get("/clinician")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "医护工作台" in response.text
    patient, patient_headers = await account(client, db_session)
    doctor, headers = await account(client, db_session, UserRole.CLINICIAN)
    await client.post("/api/v1/patient-access-grants", headers=patient_headers, json={"clinician_id": doctor["user"]["id"]})
    path = "/api/v1/clinical/patients/" + patient["user"]["id"] + "/overview"
    for params in ({"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"}, {"start": "2026-01-01T00:00:00Z", "end": "2026-05-01T00:00:00Z"}):
        assert (await client.get(path, headers=headers, params=params)).status_code == 422

async def test_dataset_filter_preserves_grants_and_counts(client, db_session):
    from antang_api.models import User
    from uuid import UUID

    patient, patient_headers = await account(client, db_session)
    doctor, doctor_headers = await account(client, db_session, UserRole.CLINICIAN)
    user = await db_session.get(User, UUID(patient['user']['id']))
    user.username = 'uci_diabetes_' + uuid4().hex[:12]
    user.username_normalized = user.username
    await db_session.commit()
    url = '/api/v1/clinical/patients'
    assert (await client.get(url, headers=doctor_headers, params={'dataset': 'uci'})).json()['total'] == 0
    await client.post('/api/v1/patient-access-grants', headers=patient_headers, json={'clinician_id': doctor['user']['id']})
    data = (await client.get(url, headers=doctor_headers, params={'dataset': 'uci', 'limit': 1})).json()
    assert data['total'] == 1
    assert data['patients'][0]['id'] == patient['user']['id']
    assert data['next_after'] is None
    assert (await client.get(url, headers=doctor_headers, params={'dataset': 'ohio'})).json()['total'] == 0
    assert (await client.get(url, headers=doctor_headers, params={'dataset': 'invalid'})).status_code == 422


async def test_example_datasets_need_no_grant_but_cannot_expose_other_patients(client, db_session):
    from uuid import UUID
    from antang_api.models.study import StudySubject

    sample, _ = await account(client, db_session)
    private, _ = await account(client, db_session)
    _, headers = await account(client, db_session, UserRole.CLINICIAN)
    now = datetime.now(UTC)
    db_session.add(StudySubject(user_id=UUID(sample['user']['id']), dataset='UCI Diabetes',
        insulin_type=None, period_start=now-timedelta(days=1), period_end=now,
        provenance={'notice': '公开历史数据'}))
    await db_session.commit()
    url = '/api/v1/clinical/patients'
    response = await client.get(url, headers=headers, params={'scope': 'examples'})
    assert response.status_code == 200
    assert [row['id'] for row in response.json()['patients']] == [sample['user']['id']]
    assert (await client.get(url+'/'+sample['user']['id']+'/overview', headers=headers,
        params={'scope': 'examples'})).status_code == 200
    assert (await client.get(url+'/'+private['user']['id']+'/overview', headers=headers,
        params={'scope': 'examples'})).status_code == 403


async def test_glucose_context_matches_same_patient_and_sample_time(client, db_session):
    from uuid import UUID
    from antang_api.models import GlucoseReading
    from antang_api.models.study import StudyEvent, StudySubject

    patient, _ = await account(client, db_session)
    other, _ = await account(client, db_session)
    _, doctor = await account(client, db_session, UserRole.CLINICIAN)
    patient_id, other_id = UUID(patient["user"]["id"]), UUID(other["user"]["id"])
    now = datetime.now(UTC)
    db_session.add(StudySubject(user_id=patient_id, dataset="OhioT1DM", insulin_type=None,
        period_start=now-timedelta(days=1), period_end=now+timedelta(days=1), provenance={}))
    point = GlucoseReading(user_id=patient_id, value_mg_dl=120, measured_at=now)
    other_point = GlucoseReading(user_id=other_id, value_mg_dl=140, measured_at=now)
    db_session.add_all([point, other_point])
    for kind, owner, hours, stop, unit in (
        ("basal", patient_id, -6, 4, "U/h"),
        ("meal", patient_id, -1, None, "g"),
        ("bolus", patient_id, 1, None, "U"),
        ("meal", patient_id, 3, None, "g"),
        ("meal", other_id, 0, None, "g"),
    ):
        db_session.add(StudyEvent(id=uuid4(), user_id=owner, kind=kind, value=1, unit=unit,
            measured_at=now+timedelta(hours=hours), ended_at=now+timedelta(hours=stop) if stop else None,
            detail="", source_ref="test", raw_record={}))
    await db_session.commit()

    root = f"/api/v1/clinical/patients/{patient_id}/glucose-records"
    path = f"{root}/{point.id}/context"
    assert (await client.get(path, headers=doctor)).status_code == 403
    response = await client.get(path, headers=doctor, params={"scope": "examples"})
    assert response.status_code == 200
    assert [event["kind"] for event in response.json()] == ["basal", "meal", "bolus"]
    assert (await client.get(f"{root}/{other_point.id}/context", headers=doctor,
        params={"scope": "examples"})).status_code == 404
    point.revoked_at = now
    await db_session.commit()
    assert (await client.get(path, headers=doctor, params={"scope": "examples"})).status_code == 404


async def test_simulated_records_move_to_existing_authorized_example(client, db_session):
    from uuid import UUID, NAMESPACE_URL, uuid5

    from antang_api.models import GlucoseReading, User
    from antang_api.models.study import StudySubject
    from antang_api.seed_clinical_demo import BEGIN, END, DATASET, NOTE, populate

    original, patient_headers = await account(client, db_session)
    extra, _ = await account(client, db_session)
    doctor, headers = await account(client, db_session, UserRole.CLINICIAN)
    original_id, extra_id = UUID(original["user"]["id"]), UUID(extra["user"]["id"])
    old = await db_session.get(User, original_id)
    added = await db_session.get(User, extra_id)
    assert old is not None and added is not None
    old.username = old.username_normalized = "antang_demo_patient"
    old.full_name = "示例患者"
    added.username = added.username_normalized = "antang_synthetic_demo_v1"
    added.full_name = "模拟患者（演示数据）"
    reading_id = uuid5(NAMESPACE_URL, "antang_demo_patient:glucose:0")
    db_session.add_all([
        StudySubject(user_id=extra_id, dataset=DATASET, insulin_type=None, period_start=BEGIN,
                     period_end=END, provenance={"notice": NOTE, "synthetic": True, "version": 1}),
        GlucoseReading(id=reading_id, user_id=extra_id, value_mg_dl=105, measured_at=BEGIN, note=NOTE),
        GlucoseReading(user_id=original_id, value_mg_dl=100, measured_at=BEGIN-timedelta(days=1)),
    ])
    await db_session.commit()
    grant = await client.post("/api/v1/patient-access-grants", headers=patient_headers,
                              json={"clinician_id": doctor["user"]["id"]})
    assert grant.status_code == 201
    await populate(db_session)
    await db_session.commit()
    await populate(db_session)
    await db_session.commit()
    root = "/api/v1/clinical/patients"
    response = await client.get(root, headers=headers)
    assert [row["id"] for row in response.json()["patients"]] == [str(original_id)]
    path = f"{root}/{original_id}"
    params = {"start": (BEGIN-timedelta(days=1)).isoformat(), "end": END.isoformat()}
    overview = (await client.get(path + "/overview", headers=headers, params=params)).json()
    assert overview["study"]["provenance"]["synthetic"] is True
    assert overview["glucose"]["summary"]["count"] == 49
    assert overview["glucose"]["points"][1]["id"] == str(reading_id)
    assert overview["study"]["event_count"] == 8
    context = await client.get(f"{path}/glucose-records/{reading_id}/context", headers=headers)
    assert [event["kind"] for event in context.json()] == ["basal"]
    assert (await client.get(path + "/overview", headers=headers, params={"scope": "examples"})).status_code == 403
    db_session.expire_all()
    assert await db_session.get(User, extra_id) is None
