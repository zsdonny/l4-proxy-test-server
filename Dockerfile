FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY server.py /app/server.py

EXPOSE 11111/tcp
EXPOSE 22222/udp

CMD ["python", "/app/server.py"]