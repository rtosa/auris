FROM python:3.12-slim AS backend-test

WORKDIR /test
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY app/ ./app/
COPY config/ ./config/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY tests/ ./tests/
ENV PYTHONPATH=/test
CMD ["python", "-m", "pytest", "-q", "tests"]

FROM node:24-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ARG BUILD_VERSION=0.23.7
ARG BUILD_ARCH=amd64

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HA_INGRESS_MODE=true \
    HOST=0.0.0.0 \
    PORT=8099

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY config/ ./config/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=frontend-build /frontend/dist ./static

RUN mkdir -p /data
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/api/v1/health', timeout=5)" || exit 1
CMD ["python", "-m", "app.main"]

