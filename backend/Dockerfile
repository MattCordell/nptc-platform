# API image, also used by the compose `migrate` service with another command.
# Build context is the repository root: the uv workspace, the lockfile and the
# Alembic config in the root pyproject.toml all live there.
#
#   docker build -f backend/Dockerfile -t nptc-backend .

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM python:3.14.7-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# The lockfile names every workspace member, so uv needs each member's
# pyproject.toml even though only the backend is installed. Dependencies go in
# a layer of their own so a source edit does not reinstall them.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY shared/pyproject.toml shared/pyproject.toml
COPY transform/pyproject.toml transform/pyproject.toml
RUN uv sync --locked --no-dev --no-install-workspace --package nptc-backend

COPY backend/src backend/src
COPY shared/src shared/src
# --no-editable installs the packages into the venv, so the runtime stage
# needs the venv alone and no source tree.
RUN uv sync --locked --no-dev --no-editable --package nptc-backend

FROM python:3.14.7-slim AS runtime
RUN groupadd --system --gid 10001 nptc \
    && useradd --system --uid 10001 --gid nptc --no-create-home nptc
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Alembic reads its configuration from the root pyproject.toml and resolves
# script_location relative to it, so both must sit at the working directory.
COPY pyproject.toml ./
COPY backend/migrations backend/migrations
COPY scripts/grant_role.py scripts/grant_role.py

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER nptc
EXPOSE 8000
CMD ["uvicorn", "nptc.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
