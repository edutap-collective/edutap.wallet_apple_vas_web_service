# syntax=docker/dockerfile:1
FROM python:3.12

ARG HTTP_PORT=8084
ENV HTTP_PORT=${HTTP_PORT}

ARG KAFKA_UI_PORT
ENV KAFKA_UI_PORT=${KAFKA_UI_PORT}

ARG BROKER_URL
ENV BROKER_URL=${BROKER_URL}

RUN echo "$HTTP_PORT"

# ARG CI_JOB_TOKEN

WORKDIR /app

COPY src /app/src
COPY pyproject.toml /app

RUN apt update
RUN apt full-upgrade -y
RUN apt install -y swig libssl-dev
# RUN python3 -m venv venv
RUN pip install -U M2Crypto
RUN pip install -U --no-cache-dir git+https://github.com/edutap-eu/edutap.wallet_apple.git
RUN pip install --no-cache-dir -e "/app[fastapi,sql,kafka]"

CMD ["sh", "-c", "uvicorn edutap.wallet_apple_vas_web_service.standalone:app --proxy-headers --host 0.0.0.0 --port $HTTP_PORT --access-log --log-level debug"]
