# Image du bot : Python 3.12 (comme le venv local), utilisateur non-root, aucun secret embarqué.
# Les clés et le token Telegram sont fournis au lancement par docker-compose (env_file: .env).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

# Dépendances d'abord (couche mise en cache tant que pyproject.toml ne change pas).
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config ./config

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data /app/reports \
    && chown -R bot:bot /app
USER bot

# Bot vivant = heartbeat de moins de 10 min dans son journal (mode passé via BOT_MODE).
HEALTHCHECK --interval=2m --timeout=20s --start-period=5m --retries=3 \
    CMD python -m bot health --mode "${BOT_MODE:-paper}" --max-age 600 || exit 1

ENTRYPOINT ["python", "-m", "bot"]
CMD ["paper"]
