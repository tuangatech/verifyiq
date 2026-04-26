# agents/registry/main.py
import os
import sqlite3
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

sys.path.insert(0, "/app")

import structlog

from fastapi import Depends, FastAPI, HTTPException, Query

from agents.shared.a2a_types import RegisterRequest, RegisteredAgent
from agents.shared.auth import require_auth
from agents.shared.logging import configure_logging

configure_logging("registry")
logger = structlog.get_logger()

DB_PATH = os.getenv("DB_PATH", "/data/registry.db")


def get_db():
    """Return a new SQLite connection with row-factory for dict-like column access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the registered_agents table if it doesn't exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registered_agents (
                url_hash         TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                url              TEXT NOT NULL UNIQUE,
                skills           TEXT NOT NULL,
                version          TEXT,
                auth_schemes     TEXT,
                capabilities      TEXT,
                agent_card_raw   TEXT NOT NULL,
                health           TEXT DEFAULT 'unknown',
                avg_latency_ms   INTEGER,
                registered_at    TEXT NOT NULL,
                last_seen        TEXT
            )
        """)
        conn.commit()
    logger.info("db_initialised", path=DB_PATH)


def make_url_hash(url: str) -> str:
    """First 16 hex chars of SHA-256 of the URL — stable, deterministic primary key."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the DB on startup; nothing to tear down on shutdown."""
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "registry", "port": 8099}


@app.post("/register", status_code=201)
def register(body: RegisterRequest, _token: str = Depends(require_auth)):
    """Upsert an agent row; returns url_hash so the caller can deregister later."""
    url_hash = make_url_hash(body.url)
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO registered_agents
                (url_hash, name, url, skills, version, auth_schemes,
                 capabilities, agent_card_raw, health, registered_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'healthy', ?, ?)
            """,
            (
                url_hash,
                body.name,
                body.url,
                json.dumps(body.skills),
                body.version,
                json.dumps(body.auth_schemes),
                json.dumps(body.capabilities),
                json.dumps(body.agent_card_raw),
                now,
                now,
            ),
        )
        conn.commit()

    logger.info("agent_registered", name=body.name, url=body.url, skills=body.skills)

    return {"url_hash": url_hash, "message": f"Registered {body.name}"}


@app.get("/agents", response_model=list[RegisteredAgent])
def list_agents(skill: str | None = Query(None)):
    """Return all agents, or only those whose skills JSON contains the requested skill."""
    with get_db() as conn:
        if skill:
            cur = conn.execute(
                "SELECT * FROM registered_agents WHERE skills LIKE ?",
                (f'%"{skill}"%',),
            )
        else:
            cur = conn.execute("SELECT * FROM registered_agents")
        rows = cur.fetchall()

    agents = []
    for row in rows:
        agents.append(
            RegisteredAgent(
                url_hash=row["url_hash"],
                name=row["name"],
                url=row["url"],
                skills=json.loads(row["skills"]),
                version=row["version"] or "",
                health=row["health"],
                avg_latency_ms=row["avg_latency_ms"],
                registered_at=row["registered_at"],
                last_seen=row["last_seen"],
            )
        )
    return agents


@app.delete("/agents/{url_hash}")
def deregister(url_hash: str, _token: str = Depends(require_auth)):
    """Remove an agent by url_hash; 404 if not found."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM registered_agents WHERE url_hash = ?",
            (url_hash,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Agent {url_hash} not found")

    return {"message": f"Deregistered {url_hash}"}
