FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV RELAYCOMM_HOST=0.0.0.0 \
    RELAYCOMM_PORT=8080 \
    RELAYCOMM_DB_PATH=/data/relaycomm.db \
    RELAYCOMM_ADAPTER=simulator

VOLUME ["/data"]
EXPOSE 8080

CMD ["relaycomm"]
