# EGOptimizer brain -- the recommend API + ingestion/training.
# Phases 1-3 need only the Python standard library, so the image is tiny.
FROM python:3.12-slim

WORKDIR /app
COPY brain/ ./brain/

# data/ (CSVs, sqlite db, model.json) is mounted as a volume at runtime.
VOLUME ["/app/data"]
EXPOSE 8787

# Health check: brain responds to /health within 10s
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8787/health', timeout=5).read()" || exit 1

# Optional config: mount brain/config.yaml to override defaults.
CMD ["python", "-m", "brain.api.server", "--host", "0.0.0.0", "--port", "8787"]
