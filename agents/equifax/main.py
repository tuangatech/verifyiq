import sys
sys.path.insert(0, "/app")

import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agents.shared.registry_client import register_with_registry, deregister_from_registry

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)

_url_hash: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _url_hash
    _url_hash = await register_with_registry()
    yield
    await deregister_from_registry(_url_hash)


app = FastAPI(lifespan=lifespan)


@app.get("/.well-known/agent.json")
def agent_card():
    return AGENT_CARD


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "equifax", "port": 8001}
