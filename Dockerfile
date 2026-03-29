FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 1001 -s /sbin/nologin appuser

WORKDIR /app

COPY server.py /app/server.py
COPY assets/ /app/assets/

EXPOSE 11111/tcp
EXPOSE 22222/udp

USER appuser
CMD ["python", "/app/server.py"]