FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir google-cloud-bigquery==3.27.0 google-cloud-aiplatform==1.75.0 google-cloud-storage==2.19.0 google-cloud-monitoring==2.24.0
COPY drift_check.py .
ENTRYPOINT ["python", "drift_check.py"]
