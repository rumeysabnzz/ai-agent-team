FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
