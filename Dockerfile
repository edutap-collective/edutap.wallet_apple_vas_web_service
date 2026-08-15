# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG HTTP_PORT=8084
ENV HTTP_PORT=${HTTP_PORT}

WORKDIR /app

COPY src /app/src
COPY pyproject.toml README.md /app/

# Every version comes from pyproject.toml, which pins exactly. This line used to read
#
#     pip install -U --no-cache-dir git+https://github.com/edutap-eu/edutap.wallet_apple.git
#
# and that is what took the service down: it installed the default branch, so each
# build produced a different image and nothing recorded which one. A warm layer cache
# hid it for twenty months; the first build in CI, with a cold cache, pulled a version
# whose settings class had been renamed and the service stopped starting.
#
# Installed non-editable: `-e` leaves the package pointing at /app/src, which only
# works while that directory is present and makes the image depend on its build context.
#
# `[fastapi,sql]` and no longer `kafka`: the extra installs aiokafka, which nothing
# imports. `kafka_producer` imports `kafka` -- a different distribution, never
# declared -- and is unreachable from the application anyway. The extra stays defined
# in pyproject.toml, so restoring it is one word once that module works.
RUN pip install --no-cache-dir "/app[fastapi,sql]"

# Neither swig, libssl-dev nor M2Crypto is here any more. edutap.wallet_apple moved to
# `cryptography` (a wheel, no compiler needed) and nothing in this package imports
# M2Crypto. Dropping them removes the build toolchain from the image.

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE ${HTTP_PORT}

CMD ["sh", "-c", "uvicorn edutap.wallet_apple_vas_web_service.standalone:app --proxy-headers --host 0.0.0.0 --port $HTTP_PORT --access-log"]
