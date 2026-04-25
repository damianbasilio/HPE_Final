FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST_SERVIDOR=0.0.0.0 \
    PUERTO_SERVIDOR=8080

WORKDIR /app

# Dependencias del sistema (gevent compila C en algunas plataformas).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN if [ -f users.json.example ] && [ ! -f users.json ]; then cp users.json.example users.json; fi

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["python", "main.py"]
