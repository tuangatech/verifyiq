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