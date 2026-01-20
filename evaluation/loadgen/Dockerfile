# Use a known-good Python runtime image (Debian slim)
FROM python:3.13-slim

# Avoid Python output buffering (so tracebacks always show in logs)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code
COPY . .

# Default command (Cloud Run Job can override, but this is safe)
ENTRYPOINT ["python", "-u", "main.py"]
