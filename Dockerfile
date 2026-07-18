# =============================================================================
# SprintShield Analytics - Production Image
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Minimal OS dependencies required by pyarrow / delta-rs wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# --- Dependency layer (cached independently of source changes) ----------------
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

# --- Application layer ---------------------------------------------------------
COPY config ./config
COPY scripts ./scripts
COPY notebooks ./notebooks
COPY .env.example ./.env.example

# Non-root runtime user.
RUN useradd --create-home --uid 1000 sprintshield \
    && mkdir -p /app/data/delta /app/reports \
    && chown -R sprintshield:sprintshield /app
USER sprintshield

# Streamlit listens here; the VS Code Simple Browser points at the same URL.
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Default: launch the Streamlit cockpit. Override with `docker run ... <cmd>`.
CMD ["streamlit", "run", "src/sprintshield/dashboard/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
