FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /srv
# Sistem içi stüdyo: ffmpeg (video kurgusu), cairo + fontlar (SVG tasarımı PNG'ye çevirme)
# Inter yazı tipi paketi her Debian sürümünde bulunmayabilir; yoksa kurulum durmaz, DejaVu kullanılır.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libcairo2 fonts-dejavu-core \
    && (apt-get install -y --no-install-recommends fonts-inter || echo "fonts-inter yok, DejaVu kullanılacak") \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8080
CMD ["python", "-m", "app.serve"]
