from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

# Make sure no real key ever leaks into a test run.
for _var in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
):
    os.environ.pop(_var, None)

from ..config import Settings  # noqa: E402
from ..models import db as dbm  # noqa: E402
from ..services.events import EventBus  # noqa: E402
from ..services.storage import DebateRepository  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+aiosqlite:///{tmp_path.as_posix()}/test.db",
        pricing_file=str(Path(__file__).resolve().parents[1] / "pricing.json"),
        min_rounds=1,
        max_rounds=3,
        consensus_threshold=0.8,
        request_timeout=5,
        max_retries=1,
        log_level="WARNING",
    )


@pytest_asyncio.fixture
async def repo(settings: Settings) -> AsyncIterator[DebateRepository]:
    sm = dbm.init_engine(settings.database_url)
    await dbm.create_all()
    try:
        yield DebateRepository(sm)
    finally:
        await dbm.dispose_engine()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()
