import os
import httpx


class NoCandidateAgentError(Exception):
    def __init__(self, skill: str):
        self.skill = skill
        super().__init__(f"No healthy agent found for skill: {skill}")


class AgentResolver:
    def __init__(self):
        self.registry_url = os.getenv("AGENT_REGISTRY_URL", "http://registry:8099")

    async def find(self, skill: str) -> str:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self.registry_url}/agents", params={"skill": skill}
            )
            response.raise_for_status()
            agents = response.json()

        # Filter to healthy or unknown agents
        candidates = [
            a for a in agents if a.get("health") in ("healthy", "unknown")
        ]
        if not candidates:
            raise NoCandidateAgentError(skill)

        # Pick lowest avg_latency_ms; treat None as infinity so new agents go last
        candidates.sort(key=lambda a: a.get("avg_latency_ms") or float("inf"))
        return candidates[0]["url"]

    async def find_all(self, skills: list[str]) -> dict[str, str]:
        return {skill: await self.find(skill) for skill in skills}

    async def list_all(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.registry_url}/agents")
                response.raise_for_status()
                return response.json()
        except Exception:
            return []
