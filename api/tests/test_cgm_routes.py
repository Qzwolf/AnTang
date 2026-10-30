from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
from antang_api.models import CgmSample, GlucoseImport
from antang_api.routers.auth import router as auth_router
from antang_api.routers.health_profile import router as health_profile_router

# 样本时间按「两小时前」算：写死日期会在几天后掉出「近 7 天」窗口，
# 测试就会在某天早上突然变红，而这跟代码没关系。
_START = datetime.now(UTC) - timedelta(hours=2)


def _stamp(offset_minutes: int) -> str:
    return (_START + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%d %H:%M")


CSV = (
    "时间,血糖,单位,趋势\n"
    f"{_stamp(0)},5.5,mmol/L,Flat\n"
    f"{_stamp(5)},6.1,mmol/L,Rising\n"
    f"{_stamp(10)},12.2,mmol/L,\n"
).encode("utf-8")


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
    username = f"cgm_{uuid4().hex[:16]}"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": username,
            "password": "correct-password",
        },
    )
    assert registered.status_code == 201
    token = registered.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_preview_is_read_only_and_shows_the_parsed_rows(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-imports/preview",
            files={"file": ("export.csv", CSV, "text/csv")},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["unit"] == "mmol/L"
        assert body["usable_rows"] == 3
        assert [row["value_mg_dl"] for row in body["samples"]] == [99, 110, 220]
        assert (
            await db_session.scalar(select(func.count()).select_from(CgmSample))
        ) == 0


async def test_importing_twice_does_not_duplicate_samples(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        first = await client.post(
            "/api/v1/health-profile/glucose-imports",
            files={"file": ("export.csv", CSV, "text/csv")},
            headers=headers,
        )
        assert first.status_code == 201
        assert first.json()["inserted_count"] == 3

        second = await client.post(
            "/api/v1/health-profile/glucose-imports",
            files={"file": ("export.csv", CSV, "text/csv")},
            headers=headers,
        )
        assert second.status_code == 201
        assert second.json()["inserted_count"] == 0
        assert second.json()["skipped_count"] == 3

        assert (
            await db_session.scalar(select(func.count()).select_from(CgmSample))
        ) == 3
        assert (
            await db_session.scalar(select(func.count()).select_from(GlucoseImport))
        ) == 2

        series = await client.get(
            "/api/v1/health-profile/cgm-samples", headers=headers
        )
        assert series.status_code == 200
        assert series.json()["count"] == 3
        assert series.json()["samples"][0]["value_mg_dl"] == 99


async def test_a_pdf_is_refused_with_a_pointer_to_the_report_import(
    db_session: AsyncSession,
) -> None:
    """CSV 这条路不收 PDF，但要把「该走哪儿」说清楚。"""

    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-imports",
            files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )

        assert response.status_code == 415
        assert "导入报告 PDF" in response.json()["detail"]


async def test_a_broken_file_is_refused_and_anonymous_callers_are_rejected(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        broken = await client.post(
            "/api/v1/health-profile/glucose-imports",
            files={"file": ("export.csv", b"name,age\nfoo,1\n", "text/csv")},
            headers=headers,
        )
        assert broken.status_code == 422

        anonymous = await client.post(
            "/api/v1/health-profile/glucose-imports",
            files={"file": ("export.csv", CSV, "text/csv")},
        )
        assert anonymous.status_code == 401
