# tests/conftest.py
import os

import pytest
import pytest_asyncio
import httpx

AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN", "verifyiq-dev-token-2026")
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Bearer token headers for protected endpoints."""
    return dict(AUTH_HEADERS)


@pytest_asyncio.fixture
async def registry_client():
    """Async HTTP client pointed at the Registry service."""
    async with httpx.AsyncClient(base_url="http://localhost:8099", timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
async def orchestrator_client():
    """Async HTTP client pointed at the Orchestrator service."""
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=10.0) as client:
        yield client


@pytest_asyncio.fixture
async def equifax_client():
    """Async HTTP client pointed at the Equifax agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=35.0) as client:
        yield client


@pytest_asyncio.fixture
async def employment_client():
    """Async HTTP client pointed at the Employment agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8002", timeout=35.0) as client:
        yield client


@pytest_asyncio.fixture
async def intl_client():
    """Async HTTP client pointed at the International agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8003", timeout=35.0) as client:
        yield client


@pytest_asyncio.fixture
async def synthesis_client():
    """Async HTTP client pointed at the Synthesis agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8004", timeout=35.0) as client:
        yield client


@pytest_asyncio.fixture
async def mortgage_platform_client():
    """Async HTTP client pointed at the Mortgage Platform service."""
    async with httpx.AsyncClient(base_url="http://localhost:9000", timeout=120.0) as client:
        yield client