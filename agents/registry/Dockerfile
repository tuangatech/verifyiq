FROM python:3.12.5-slim-bookworm

WORKDIR /app

# Copy shared types first so they're available to all agents
COPY agents/shared/ /app/agents/shared/

COPY agents/registry/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agents/registry/ /app/

EXPOSE 8099

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8099"]
