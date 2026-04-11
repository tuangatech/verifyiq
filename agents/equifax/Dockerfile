FROM python:3.12.5-slim-bookworm

WORKDIR /app

COPY agents/shared/ /app/agents/shared/

COPY agents/equifax/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agents/equifax/ /app/

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
