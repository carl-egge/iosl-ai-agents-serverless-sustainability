FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY loadgen_job.py /app/loadgen_job.py
COPY function_urls.example.json /app/function_urls.example.json
COPY hourly_region_map.example.json /app/hourly_region_map.example.json

CMD ["python", "/app/loadgen_job.py"]
