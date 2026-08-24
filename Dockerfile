FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY bot.py README.md .dockerignore ./
RUN useradd --system --uid 10001 botuser \
    && mkdir -p /data \
    && chown -R botuser:botuser /app /data

USER 10001:10001
ENV DATABASE_PATH=/data/applications.db

CMD ["python", "bot.py"]