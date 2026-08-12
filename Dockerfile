# Base image pinnutý digestem: `python:3.12-slim` se přepisuje, digest ne.
FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS builder

COPY --from=ghcr.io/astral-sh/uv@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Závislosti zvlášť od kódu: změna v src/ neinvaliduje cache instalace.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 etl
COPY --from=builder --chown=etl:etl /app /app
WORKDIR /app
USER etl

# Výchozí entrypoint je CLI, protože ten příkaz píše člověk:
#   docker run --rm -v "$PWD/data:/app/data" nyc-taxi-etl run --year 2025 --month 1
# Lambda si entrypoint přebíjí v ImageConfig na `python -m awslambdaric`
# s commandem `app.lambda_handler.handler` -- v image tím nezůstává žádný kompromis.
ENTRYPOINT ["python", "-m", "app"]
CMD ["--help"]
