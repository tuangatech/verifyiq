FROM python:3.12.5-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY agents/shared/ /app/agents/shared/

# Copy all agents to /app/agents/ for imports
COPY agents/ /app/agents/

COPY agents/orchestrator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agents/orchestrator/ /app/orchestrator/

EXPOSE 8000

CMD ["python", "-m", "orchestrator"]
