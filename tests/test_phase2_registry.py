# tests/test_phase2_registry.py
"""
Phase 2 integration tests — Agent Registry, self-registration, AgentResolver.

Run against the live Docker Compose stack:
    pytest tests/test_phase2_registry.py -v

All 11 tests must pass before Phase 3.
"""

import subprocess
import time

import pytest
import httpx

REGISTRY_URL = "http://localhost:8099"
ORCHESTRATOR_URL = "http://localhost:8000"

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _docker_compose_restart(service: str) -> None:
    """Restart a single service and wait for it to become healthy."""
    subprocess.run(
        ["docker", "compose", "restart", service],
        check=True,
        capture_output=True,
    )
    # Give the Registry a moment to re-register agents before we query
    time.sleep(6)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def test_equifax_registered(registry_client: httpx.AsyncClient):
    """GET /agents?skill=credit_score returns exactly 1 agent."""
    response = await registry_client.get("/agents", params={"skill": "credit_score"})
    assert response.status_code == 200, response.text
    agents = response.json()
    assert len(agents) == 1, f"Expected 1 agent for credit_score, got {len(agents)}: {agents}"
    assert agents[0]["name"] == "Equifax Credit Agent"
    assert agents[0]["url"] == "http://equifax:8001"


async def test_all_five_agents_registered(registry_client: httpx.AsyncClient):
    """GET /agents (no filter) returns all 5 agents."""
    response = await registry_client.get("/agents")
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) == 5, f"Expected 5 agents, got {len(agents)}: {agents}"

    urls = {a["url"] for a in agents}
    expected = {
        "http://equifax:8001",
        "http://employment:8002",
        "http://intl:8003",
        "http://synthesis:8004",
        "http://orchestrator:8000",
    }
    assert urls == expected, f"URL mismatch: {urls} vs {expected}"


async def test_skill_filter_employment(registry_client: httpx.AsyncClient):
    """GET /agents?skill=employment_status returns Employment Agent only."""
    response = await registry_client.get("/agents", params={"skill": "employment_status"})
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) == 1
    assert agents[0]["name"] == "Employment Verification Agent"


async def test_skill_filter_synthesis(registry_client: httpx.AsyncClient):
    """GET /agents?skill=risk_synthesis returns exactly 1 agent."""
    response = await registry_client.get("/agents", params={"skill": "risk_synthesis"})
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) == 1


async def test_nonexistent_skill_returns_empty(registry_client: httpx.AsyncClient):
    """GET /agents?skill=nonexistent_xyz_skill returns [] with status 200."""
    response = await registry_client.get("/agents", params={"skill": "nonexistent_xyz_skill"})
    assert response.status_code == 200
    assert response.json() == []


async def test_registration_is_idempotent(registry_client: httpx.AsyncClient):
    """POST the same payload twice results in exactly 1 agent (upsert, not duplicate)."""
    dup_skill = "dup_test_skill_123"
    payload = {
        "name": "Duplicate Test Agent",
        "url": "http://duptest:9999",
        "skills": [dup_skill],
        "version": "1.0.0",
        "auth_schemes": [],
        "capabilities": {},
        "agent_card_raw": {},
    }

    # Register twice
    r1 = await registry_client.post("/register", json=payload)
    assert r1.status_code == 201, r1.text
    r2 = await registry_client.post("/register", json=payload)
    assert r2.status_code == 201, r2.text

    # Should appear exactly once
    agents = await registry_client.get("/agents", params={"skill": dup_skill})
    assert agents.status_code == 200
    matches = agents.json()
    assert len(matches) == 1, f"Expected 1 agent, got {len(matches)}: {matches}"

    # Clean up
    url_hash = r1.json()["url_hash"]
    await registry_client.delete(f"/agents/{url_hash}")


async def test_deregister_removes_agent(registry_client: httpx.AsyncClient):
    """Register, verify present, DELETE, verify gone."""
    payload = {
        "name": "Temp Test Agent",
        "url": "http://tempagent:9998",
        "skills": ["temp_skill_456"],
        "version": "1.0.0",
        "auth_schemes": [],
        "capabilities": {},
        "agent_card_raw": {},
    }

    # Register
    r = await registry_client.post("/register", json=payload)
    assert r.status_code == 201, r.text
    url_hash = r.json()["url_hash"]

    # Verify present
    agents = await registry_client.get("/agents", params={"skill": "temp_skill_456"})
    assert agents.status_code == 200
    assert len(agents.json()) == 1

    # Deregister
    dr = await registry_client.delete(f"/agents/{url_hash}")
    assert dr.status_code == 200, dr.text

    # Verify gone
    agents = await registry_client.get("/agents", params={"skill": "temp_skill_456"})
    assert agents.status_code == 200
    assert agents.json() == []


async def test_registry_persists_across_restart(registry_client: httpx.AsyncClient):
    """Agent count is the same before and after Registry container restart."""
    # Count before
    before = await registry_client.get("/agents")
    count_before = len(before.json())

    # Restart Registry
    _docker_compose_restart("registry")

    # Count after
    after = await registry_client.get("/agents")
    count_after = len(after.json())

    assert count_before == count_after == 5, (
        f"Before={count_before}, after={count_after} (expected 5)"
    )


async def test_orchestrator_proxies_registry(orchestrator_client: httpx.AsyncClient):
    """GET /agents on orchestrator returns the same agent list as the Registry."""
    async with httpx.AsyncClient(base_url=REGISTRY_URL, timeout=10.0) as client:
        reg_resp = await client.get("/agents")
    orch_resp = await orchestrator_client.get("/agents")

    assert orch_resp.status_code == 200
    reg_agents = reg_resp.json()
    orch_agents = orch_resp.json()

    assert len(orch_agents) == len(reg_agents) == 5

    reg_urls = {a["url"] for a in reg_agents}
    orch_urls = {a["url"] for a in orch_agents}
    assert orch_urls == reg_urls


async def test_orchestrator_resolve_debug_endpoint(orchestrator_client: httpx.AsyncClient):
    """GET /resolve/credit_score returns the correct Equifax URL."""
    response = await orchestrator_client.get("/resolve/credit_score")
    assert response.status_code == 200
    data = response.json()
    assert data["skill"] == "credit_score"
    assert data["url"] == "http://equifax:8001"


async def test_orchestrator_resolve_unknown_skill(orchestrator_client: httpx.AsyncClient):
    """GET /resolve/no_such_skill returns a JSON object with an error key (not 500)."""
    response = await orchestrator_client.get("/resolve/no_such_skill")
    assert response.status_code == 200
    data = response.json()
    assert "error" in data, f"Expected 'error' key in response, got: {data}"