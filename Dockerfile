# SmartBike AI - serving image (REST API + operator app). One image, two entrypoints (see docker-compose.yml).
# Data (Gold tables) and the exported model bundle are mounted read-only: the image holds code only, so the same
# image runs against local Parquet exports, a Fabric OneLake shortcut sync or any other copy of the Gold layer.
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SMARTBIKE_ROOT=/srv/smartbike \
    SMARTBIKE_MODEL_DIR=/srv/smartbike/models/serving \
    MLFLOW_DISABLE_AGENT_HINT=1 \
    OMP_NUM_THREADS=1
# OMP_NUM_THREADS=1: a 1-row prediction gains nothing from OpenMP, and N concurrent requests x N cores threads
# oversubscribe the CPU (measured: /v1/predict throughput fell from 14 req/s to 6 req/s at concurrency 8).

WORKDIR /srv/smartbike

# dependencies first: cached layer, rebuilt only when the lock file changes
COPY requirements/serving.txt requirements/serving.txt
RUN pip install -r requirements/serving.txt

COPY smartbike/ smartbike/
COPY app/ app/
COPY assets/ assets/
COPY .streamlit/ .streamlit/

# least privilege: never run the services as root
RUN useradd --create-home --uid 10001 smartbike && mkdir -p data/lakehouse/Tables models/serving \
    && chown -R smartbike:smartbike /srv/smartbike
USER smartbike

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1
# One process per container, scaled with replicas behind a load balancer (App Service instances, compose --scale).
# Measured: `--workers 2` added ~45 ms to every keep-alive request (docs/benchmark.md); the access log duplicates
# the structured JSON request log of app/api.py.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
