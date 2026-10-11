# One image for api, app, seed and loop (they differ only in the command; see docker-compose.yml).
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# libgomp1: LightGBM runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 clinova
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    CLINOVA_MODE=demo MODEL_VERSION=v1.0 STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY clinova ./clinova
COPY scripts/seed.py ./scripts/
COPY db ./db
COPY docs ./docs
COPY tests/fixtures ./tests/fixtures
RUN uv sync --frozen --no-dev \
    && mkdir -p models .data/models .data/model \
    && cp -r db/seed/files/bundle/. models/ \
    && cp db/seed/files/surrogate.json .data/models/ \
    && cp db/seed/files/cohort_any_2000_42.parquet .data/model/ \
    && chown -R clinova:clinova /app
USER clinova
EXPOSE 8000 8501
CMD ["python", "-m", "clinova.api", "--host", "0.0.0.0"]
