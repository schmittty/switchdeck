FROM python:3.12-slim

# Only optional dependency: `cryptography` for Fernet encryption of stored switch passwords.
# (Without it SwitchDeck falls back to an HMAC'd stream cipher; with it, credentials use AES.)
RUN pip install --no-cache-dir cryptography==43.* \
 && useradd -r -u 1000 switchdeck

WORKDIR /opt/switchdeck
COPY app ./app
COPY tools ./tools

ENV SD_DATA_DIR=/data \
    SD_HTTP_PORT=8080 \
    SD_FW_PORT=8081 \
    SD_SYSLOG_PORT=5514 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data && chown switchdeck /data
VOLUME ["/data"]
EXPOSE 8080 8081 5514/udp

USER switchdeck
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import socket,os;socket.create_connection(('127.0.0.1',int(os.environ['SD_HTTP_PORT'])),3).close()" || exit 1
CMD ["python", "-m", "app.main"]
