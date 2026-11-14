FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy MPLCONFIGDIR=/tmp/payrevive-mpl PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY scripts ./scripts
COPY simulator ./simulator
COPY ml ./ml
COPY experiments ./experiments
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home appuser \
    && install -d -o appuser -g appuser /home/appuser/demo-reports
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
RUN uv sync --frozen
USER appuser
