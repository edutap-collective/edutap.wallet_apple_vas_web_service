# syntax=docker/dockerfile:1
# Two stages: the build installs the package, the runtime image carries only the
# result. Plain `pip install` on purpose -- `uv` belongs in the development
# environment, not in a container image.
FROM python:3.14-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# No git and no build toolchain. Every version comes from pyproject.toml, which pins
# exactly and names only releases on PyPI. This line used to read
#
#     pip install -U --no-cache-dir git+https://github.com/edutap-eu/edutap.wallet_apple.git
#
# and that is what took the service down: it installed the default branch, so each
# build produced a different image and nothing recorded which one. A warm layer cache
# hid it for twenty months; the first build in CI, with a cold cache, pulled a version
# whose settings class had been renamed and the service stopped starting.
#
# swig, libssl-dev and M2Crypto are gone with it -- edutap.wallet_apple moved to
# `cryptography`, which ships wheels, and nothing here imports M2Crypto.
RUN pip install --no-cache-dir ".[fastapi,sql]"

FROM python:3.14-slim
# The interpreter of the base image is 3.14, so this is where `pip install` put the
# package in the build stage. Changing the base image tag means changing this path --
# renovate.json5 says so on the rule that proposes the bump.
COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

# The migrations ride along with the service they belong to. The estate splits
# the job: `edutap.db_definitions` migrates the `public` contract schema, and
# every package-owned schema is applied by `alembic upgrade head` run out of the
# package's own image, before the service starts -- under a role that has
# CREATE, which the service itself does not. Without these two paths in the
# image there is no way to create `wallet_apple_vas` at all, and the service
# starts cleanly and then 500s on its first request.
#
# Not installed as package data: `alembic.ini` is deployment configuration and
# the revisions are not importable modules. Copying them keeps `alembic -c
# /app/alembic.ini upgrade head` working from this WORKDIR.
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

ARG HTTP_PORT=8084
ENV HTTP_PORT=${HTTP_PORT}

RUN useradd --create-home --uid 10001 app
WORKDIR /app
USER app
EXPOSE ${HTTP_PORT}

# No --log-level debug: the previous default logged every request body at DEBUG, and
# this service handles authentication headers and device push tokens.
CMD ["sh", "-c", "uvicorn edutap.wallet_apple_vas_web_service.standalone:app --proxy-headers --host 0.0.0.0 --port $HTTP_PORT --access-log"]
