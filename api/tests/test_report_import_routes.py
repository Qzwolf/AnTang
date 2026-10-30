"""报告 PDF 的导入接口：先预览、确认才入库。

这里把 `parse_report_pdf` 换成一个固定的 `ParsedReport`——接口这一层要验的是
「拒收什么、返回什么、谁能看到」，解析本身在 test_report_parse.py 里单独测。
"""

from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
from antang_api.health_profile.report_parse import ParsedReport
from antang_api.models import GlucoseReport, MessageAttachment
from antang_api.routers.auth import router as auth_router
from antang_api.routers.health_profile import router as health_profile_router

PARSED = ParsedReport(
    period_start=date(2025, 7, 28),
    period_end=date(2025, 7, 31),
    generated_at=date(2025, 7, 31),
    device="硅基动感Pro",
    device_serial="LT2410FE3P",
    hospital="示例医院",
    mean_mmol=5.81,
    sd_mmol=0.9,
    cv_percent=15.53,
    ehba1c_percent=5.34,
    normal_percent=99.6,
    low_percent=0.4,
)


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
    username = f"report_{uuid4().hex[:16]}"
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


async def test_a_preview_shows_what_we_read_without_saving(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "antang_api.routers.health_profile.parse_report_pdf", lambda _: PARSED
    )
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports/preview",
            files={"file": ("report.pdf", b"%PDF-1.7 fake", "application/pdf")},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["file_name"] == "report.pdf"
        assert body["device"] == "硅基动感Pro"
        assert body["mean_mmol"] == pytest.approx(5.81)
        # 没读到的要说没读到，而不是报 0。
        assert "高分档" in body["missing"]
        assert "正常分档" not in body["missing"]
        assert (
            await db_session.scalar(select(func.count()).select_from(GlucoseReport))
        ) == 0


async def test_a_preview_refuses_a_csv(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports/preview",
            files={"file": ("export.csv", b"time,value\n", "text/csv")},
            headers=headers,
        )

        assert response.status_code == 415
        assert "CSV" in response.json()["detail"]


async def test_a_preview_refuses_an_empty_file(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports/preview",
            files={"file": ("report.pdf", b"", "application/pdf")},
            headers=headers,
        )

        assert response.status_code == 422
        assert "空" in response.json()["detail"]


async def test_an_unreadable_report_says_why(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from antang_api.health_profile.report_parse import UnreadableReport

    def explode(_: bytes) -> ParsedReport:
        raise UnreadableReport("这个 PDF 里没有文字层（大概率是扫描件或截图），读不出数字。")

    monkeypatch.setattr("antang_api.routers.health_profile.parse_report_pdf", explode)
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports/preview",
            files={"file": ("scan.pdf", b"%PDF-1.7 fake", "application/pdf")},
            headers=headers,
        )

        assert response.status_code == 422
        assert "扫描件" in response.json()["detail"]


async def test_confirming_a_report_keeps_the_file_and_saves_one_summary(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认导入要吃两样东西：这份 PDF 自己（以后还能打开），和它的指标速览。"""

    monkeypatch.setattr(
        "antang_api.routers.health_profile.parse_report_pdf", lambda _: PARSED
    )
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        created = await client.post(
            "/api/v1/health-profile/glucose-reports",
            files={"file": ("cgm.pdf", b"%PDF-1.7 fake", "application/pdf")},
            headers=headers,
        )

        assert created.status_code == 201
        body = created.json()
        assert body["period_end"] == "2025-07-31"
        assert body["mean_mmol"] == pytest.approx(5.81)
        # 高位百分比不写进去，TAR 就只算「高」这一档（报告没印就是没印）。
        assert body["tbr_percent"] == pytest.approx(0.4)
        # 文件留下来了，所以详情页的「打开原报告」有得开。
        assert body["source_attachment_id"] is not None

        attachment = await db_session.get(MessageAttachment, body["source_attachment_id"])
        assert attachment is not None
        assert attachment.message_id is None
        assert attachment.data == b"%PDF-1.7 fake"
        assert attachment.filename == "cgm.pdf"

        listed = await client.get(
            "/api/v1/health-profile/glucose-reports", headers=headers
        )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

        # 另一双眼睛看不到它。
        other = await _patient_headers(client)
        theirs = await client.get(
            "/api/v1/health-profile/glucose-reports", headers=other
        )
        assert theirs.status_code == 200
        assert theirs.json()["items"] == []


async def test_confirming_refuses_a_csv_file(db_session: AsyncSession) -> None:
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports",
            files={"file": ("export.csv", b"time,value\n", "text/csv")},
            headers=headers,
        )

        assert response.status_code == 415
        assert "PDF" in response.json()["detail"]


async def test_confirming_an_unreadable_report_saves_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from antang_api.health_profile.report_parse import UnreadableReport

    def explode(_: bytes) -> ParsedReport:
        raise UnreadableReport("这个 PDF 里没有文字层（大概率是扫描件或截图），读不出数字。")

    monkeypatch.setattr("antang_api.routers.health_profile.parse_report_pdf", explode)
    async for client in _client(db_session):
        headers = await _patient_headers(client)
        response = await client.post(
            "/api/v1/health-profile/glucose-reports",
            files={"file": ("scan.pdf", b"%PDF-1.7 fake", "application/pdf")},
            headers=headers,
        )

        assert response.status_code == 422
        assert "扫描件" in response.json()["detail"]
        assert (
            await db_session.scalar(select(func.count()).select_from(GlucoseReport))
        ) == 0
        assert (
            await db_session.scalar(
                select(func.count()).select_from(MessageAttachment)
            )
        ) == 0
