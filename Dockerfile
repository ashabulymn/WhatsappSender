# WhatsappSender — Dokploy deployment (Streamlit dashboard)
# Fork dari SoCloseSociety/WhatsappSender untuk pengembangan & referensi.
FROM python:3.11-slim

WORKDIR /app

# Timezone Asia/Jakarta (untuk scheduler pesan terjadwal)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Jakarta

# Dependencies dulu (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source
COPY . .

# Streamlit dashboard + GoWA webhook receiver (Balasan Masuk)
EXPOSE 8501 8000

# DB SQLite disimpan di volume persisten (/app/data) bila DB_PATH diarahkan ke sana
# start.sh menjalankan: uvicorn webhook:app (WEBHOOK_PORT, default 8000)
#                      + streamlit dashboard (DASHBOARD_PORT, default 8501)
CMD ["bash", "start.sh"]
