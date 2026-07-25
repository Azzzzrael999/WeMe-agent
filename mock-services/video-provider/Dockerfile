ARG PYTHON_RUNTIME_IMAGE=python:3.11-slim
FROM ${PYTHON_RUNTIME_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8010

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --chown=app:app server.py ./server.py

USER app
EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=3s --retries=20 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2)"]

CMD ["python", "server.py"]
