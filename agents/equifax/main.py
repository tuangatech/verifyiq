import sys
sys.path.insert(0, "/app")

import json
from pathlib import Path
from fastapi import FastAPI

app = FastAPI()

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)


@app.get("/.well-known/agent.json")
def agent_card():
    return AGENT_CARD


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "equifax", "port": 8001}
