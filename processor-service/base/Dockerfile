FROM --platform=$BUILDPLATFORM python:3.13-slim

RUN apt-get update && apt-get install -y \
    ca-certificates --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt
