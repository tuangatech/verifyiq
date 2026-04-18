# agents/orchestrator/resolver.py
import asyncio
import os
import httpx


class NoCandidateAgentError(Exception):
    def __init__(self, skill: str):
        self.skill = skill
        super().__init__(f"No healthy agent found for skill: {skill}")


class AgentResolver:
    """Resolves skill names to agent URLs via the Registry."""

    def __init__(self):
        self.registry_url = os.getenv("AGENT_REGISTRY_URL", "http://registry:8099")

    async def find(self, skill: str) -> str:
        """Query Registry for agents that advertise the given skill, then return the
        URL of the healthiest, lowest-latency candidate.

        Skill matching is exact (case-sensitive) — the Registry does a JSON substring
        match: WHERE skills LIKE '%"<skill>"%'. Pass the skill name verbatim as
        declared in each agent's Agent Card (e.g. "credit_score", not "Credit_Score").
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self.registry_url}/agents", params={"skill": skill}
            )
            response.raise_for_status()
            agents = response.json()  # Registry has already filtered to skill matches

        # Exclude agents the Registry has marked as unhealthy (e.g. failed health check)
        # "unknown" is accepted — newly registered agents haven't been probed yet
        candidates = [
            a for a in agents if a.get("health") in ("healthy", "unknown")
        ]
        if not candidates:
            raise NoCandidateAgentError(skill)

        # None avg_latency_ms → treat as infinity so newly-registered agents sort last
        candidates.sort(key=lambda a: a.get("avg_latency_ms") or float("inf"))
        return candidates[0]["url"]

    async def find_all(self, skills: list[str]) -> dict[str, str]:
        """Resolve multiple skills in parallel; raises NoCandidateAgentError if any skill has no candidates."""
        urls = await asyncio.gather(*[self.find(skill) for skill in skills])
        return dict(zip(skills, urls))

    async def list_all(self) -> list[dict]:
        """Return all registered agents; returns [] on any error (never raises)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.registry_url}/agents")
                response.raise_for_status()
                return response.json()
        except Exception:
            return []
