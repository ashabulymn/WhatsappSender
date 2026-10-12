# WhatsappSender — Dokploy deployment (Streamlit dashboard)
# Fork dari SoCloseSociety/WhatsappSender untuk pengembangan & referensi.
FROM python:3.11-slim

WORKDIR /app

# Dependencies dulu (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source
COPY . .

# Streamlit dashboard
EXPOSE 8501

# DB SQLite disimpan di volume persisten (/app/data) bila DB_PATH diarahkan ke sana
CMD ["streamlit", "run", "dashboard.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
