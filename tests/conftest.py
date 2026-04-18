# tests/conftest.py
import pytest
import pytest_asyncio
import httpx


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
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=15.0) as client:
        yield client


@pytest_asyncio.fixture
async def employment_client():
    """Async HTTP client pointed at the Employment agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8002", timeout=15.0) as client:
        yield client


@pytest_asyncio.fixture
async def intl_client():
    """Async HTTP client pointed at the International agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8003", timeout=15.0) as client:
        yield client


@pytest_asyncio.fixture
async def synthesis_client():
    """Async HTTP client pointed at the Synthesis agent."""
    async with httpx.AsyncClient(base_url="http://localhost:8004", timeout=15.0) as client:
        yield client