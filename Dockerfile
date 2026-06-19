# EGOptimizer brain -- the recommend API + ingestion/training.
# Phases 1-3 need only the Python standard library, so the image is tiny.
FROM python:3.12-slim

WORKDIR /app
COPY brain/ ./brain/

# data/ (CSVs, sqlite db, model.json) is mounted as a volume at runtime.
VOLUME ["/app/data"]
EXPOSE 8787

# Optional config: mount brain/config.yaml to override defaults.
CMD ["python", "-m", "brain.api.server", "--host", "0.0.0.0", "--port", "8787"]
