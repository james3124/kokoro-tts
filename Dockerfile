FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HOME=/app/hf_cache
COPY warmup.py .
RUN python warmup.py

COPY . .

CMD uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
