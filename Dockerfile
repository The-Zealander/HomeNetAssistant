FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SARAH_HOST=0.0.0.0 \
    SARAH_PORT=8000

WORKDIR /app
COPY requirements-server.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-server.txt

COPY --chown=10001:10001 . .
RUN mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001:10001

EXPOSE 8000
CMD ["python", "main.py", "--server"]
