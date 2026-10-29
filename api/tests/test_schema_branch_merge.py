"""Both independently deployed migration chains must converge without data loss."""

import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from antang_api.database import Base
from antang_api.settings import settings

API = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("parent", ["k48e9f1a6b52", "q64b9d6e0c83"])
async def test_existing_local_and_server_branches_upgrade_without_losing_users(parent):
    # The outer test runner already supplied a temporary test database. Never run
    # this migration rehearsal through a development or production connection.
    original = make_url(settings.database_url)
    assert original.database is not None and original.database.startswith(
        "antang_test_"
    )
    name = "antang_test_merge_" + uuid4().hex
    url = original.set(database=name).render_as_string(hide_password=False)
    admin = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    database = create_async_engine(url)
    user_id = uuid4()

    async def upgrade(revision):
        environment = dict(os.environ, DATABASE_URL=url)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            revision,
            cwd=API,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, (stdout + stderr).decode()

    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        await upgrade(parent)
        async with database.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, username, username_normalized, password_hash) "
                    "VALUES (:id, 'merge-existing-user', 'merge-existing-user', 'test-only')"
                ),
                {"id": user_id},
            )
            if parent == "k48e9f1a6b52":
                await connection.execute(
                    text(
                        "UPDATE users SET role = 'clinician', full_name = '保留姓名' WHERE id = :id"
                    ),
                    {"id": user_id},
                )
        await upgrade("head")
        async with database.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT role, full_name FROM users WHERE id = :id"),
                    {"id": user_id},
                )
            ).one()
            assert row.role == ("clinician" if parent == "k48e9f1a6b52" else "patient")
            assert row.full_name == ("保留姓名" if parent == "k48e9f1a6b52" else None)
            tables = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            assert set(Base.metadata.tables) <= tables
            assert (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one() == "v19ae2b35d48"
    finally:
        await database.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


def test_unified_schema_has_one_head():
    config = Config(str(API / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == ["v19ae2b35d48"]
