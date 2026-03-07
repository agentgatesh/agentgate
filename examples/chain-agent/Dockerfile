FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY agent.py .

EXPOSE 9002

CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "9002"]
