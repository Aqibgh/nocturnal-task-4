FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; the mounted data/state/out folders are the only writable paths.
RUN useradd --create-home recon && chown -R recon /app
USER recon

ENTRYPOINT ["python", "-m", "recon"]
CMD ["run"]

# Tests and code-quality tools; never shipped in the app image.
FROM app AS dev
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER recon
