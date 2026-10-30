from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
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
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _patient_headers(client: AsyncClient) -> dict[str, str]:
    username = f"meal_{uuid4().hex[:16]}"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": username,
            "password": "correct-password",
        },
    )
    assert registered.status_code == 201, registered.text
    token = registered.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_a_meal_is_logged_listed_and_deleted(client: AsyncClient) -> None:
    headers = await _patient_headers(client)

    created = await client.post(
        "/api/v1/health-profile/meal-logs",
        headers=headers,
        json={
            "eaten_at": "2026-10-24T12:30:00+08:00",
            "meal_type": "lunch",
            "description": "一碗米饭、青菜、鸡胸",
            "carbs_grams": 65,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["meal_type"] == "lunch"
    assert body["carbs_grams"] == 65

    listed = await client.get("/api/v1/health-profile/meal-logs", headers=headers)
    assert listed.status_code == 200
    assert [row["description"] for row in listed.json()] == ["一碗米饭、青菜、鸡胸"]

    removed = await client.delete(
        f"/api/v1/health-profile/meal-logs/{body['id']}", headers=headers
    )
    assert removed.status_code == 204

    again = await client.get("/api/v1/health-profile/meal-logs", headers=headers)
    assert again.json() == []


async def test_carbs_are_optional_and_time_needs_a_timezone(
    client: AsyncClient,
) -> None:
    headers = await _patient_headers(client)

    plain = await client.post(
        "/api/v1/health-profile/meal-logs",
        headers=headers,
        json={
            "eaten_at": "2026-10-24T08:00:00+08:00",
            "meal_type": "snack",
            "description": "半个苹果",
        },
    )
    assert plain.status_code == 201, plain.text
    assert plain.json()["carbs_grams"] is None

    naive = await client.post(
        "/api/v1/health-profile/meal-logs",
        headers=headers,
        json={
            "eaten_at": "2026-10-24T08:00:00",
            "meal_type": "snack",
            "description": "没带时区",
        },
    )
    assert naive.status_code == 422


async def test_a_medication_is_logged_with_the_text_the_patient_typed(
    client: AsyncClient,
) -> None:
    headers = await _patient_headers(client)

    created = await client.post(
        "/api/v1/health-profile/medication-logs",
        headers=headers,
        json={
            "taken_at": "2026-10-24T07:50:00+08:00",
            "name": "诺和锐",
            "dose_text": "6 单位",
            "kind": "insulin",
            "note": "早餐前",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "诺和锐"
    assert body["dose_text"] == "6 单位"

    listed = await client.get(
        "/api/v1/health-profile/medication-logs", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()[0]["kind"] == "insulin"

    removed = await client.delete(
        f"/api/v1/health-profile/medication-logs/{body['id']}", headers=headers
    )
    assert removed.status_code == 204


async def test_medication_and_meals_need_a_patient_account(
    client: AsyncClient,
) -> None:
    anonymous = await client.get("/api/v1/health-profile/meal-logs")
    assert anonymous.status_code == 401
    anonymous_logs = await client.get("/api/v1/health-profile/medication-logs")
    assert anonymous_logs.status_code == 401


async def test_a_meal_that_is_not_yours_is_not_deletable(
    client: AsyncClient,
) -> None:
    mine = await _patient_headers(client)
    theirs = await _patient_headers(client)

    created = await client.post(
        "/api/v1/health-profile/meal-logs",
        headers=mine,
        json={
            "eaten_at": "2026-10-24T12:30:00+08:00",
            "meal_type": "lunch",
            "description": "别人的饭",
        },
    )
    meal_id = created.json()["id"]

    attempt = await client.delete(
        f"/api/v1/health-profile/meal-logs/{meal_id}", headers=theirs
    )
    assert attempt.status_code == 404
