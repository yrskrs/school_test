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

# Expose port
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
