FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render (and most PaaS targets) inject $PORT at runtime; 8000 is just the
# local-docker-run default.
ENV PORT=8000
EXPOSE 8000

# Default process is the web server. Background-job services (see
# render.yaml) override this with `python run_job.py <job>` or
# `python worker.py`, reusing the same image.
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --timeout 60"]
