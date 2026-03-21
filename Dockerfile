FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server.py /app/server.py
COPY bigbuckbunny.ts /app/bigbuckbunny.ts

EXPOSE 11111/tcp
EXPOSE 22222/udp

CMD ["python", "/app/server.py"]