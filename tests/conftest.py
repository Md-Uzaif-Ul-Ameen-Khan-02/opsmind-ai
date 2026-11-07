"""Test configuration.

A throwaway SQLite database is created before importing the app. Demo mode is
disabled so tests control seeding explicitly, and no real credentials are used.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

os.environ["DEMO_MODE"] = "false"
os.environ["DATABASE_URL"] = (
    f"sqlite:///{(Path(tempfile.gettempdir()) / f'opsmind_test_{uuid.uuid4().hex}.db').as_posix()}"
)
# Empty (not missing) so a developer's real .env credentials are overridden and
# the test suite can never make live, paid API calls.
os.environ["HINDSIGHT_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seeding import seed_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
