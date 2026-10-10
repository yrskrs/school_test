FROM python:3.11-slim

# Prevent writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (for psycopg2, etc)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
# Note: We exclude PyQt6 because it's only needed for the local GUI
COPY requirements.txt .
RUN grep -v "PyQt6" requirements.txt > req_docker.txt \
    && pip install --no-cache-dir -r req_docker.txt

# Copy the rest of the application
COPY . .
RUN mkdir -p /app/data /app/app/static/tests /app/app/static/uploads
# Fail the build before deployment if an installed PostgreSQL driver or the
# application import is broken. This uses synthetic URLs and never connects.
RUN python scripts/check_runtime.py

# Expose port
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

ARG VCS_REF=local
ARG APP_VERSION=1.4.0
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/yrskrs/school_test"
