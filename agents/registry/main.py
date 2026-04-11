import sys
sys.path.insert(0, "/app")

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "registry", "port": 8099}


@app.get("/agents")
def list_agents():
    return []
