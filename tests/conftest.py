from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from orgrebase.fixture import EnterpriseFixture, load_fixture
from orgrebase.service import OrgRebaseService


@pytest.fixture
def fixture() -> EnterpriseFixture:
    return load_fixture()


@pytest.fixture
def service() -> OrgRebaseService:
    runtime = OrgRebaseService()
    yield runtime
    runtime.store.close()
