import os
import sqlite3
import hashlib
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from fastapi import FastAPI, HTTPException, Query
from contextlib import contextmanager

from agents.shared.a2a_types import RegisterRequest, RegisteredAgent

app = FastAPI()

DB_PATH = os.getenv("DB_PATH", "/data/registry.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
    print(f"[Registry] Database initialised at {DB_PATH}")


def make_url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "registry", "port": 8099}


@app.post("/register", status_code=201)
def register(body: RegisterRequest):
    url_hash = make_url_hash(body.url)
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cur = conn.execute(
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

    skills_str = ", ".join(body.skills)
    print(f"[Registry] Registered: {body.name} @ {body.url} | skills: {skills_str}")

    return {"url_hash": url_hash, "message": f"Registered {body.name}"}


@app.get("/agents", response_model=list[RegisteredAgent])
def list_agents(skill: str | None = Query(None)):
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
def deregister(url_hash: str):
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM registered_agents WHERE url_hash = ?",
            (url_hash,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Agent {url_hash} not found")

    return {"message": f"Deregistered {url_hash}"}
