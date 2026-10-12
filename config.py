"""
WhatsApp Bulk Sender — Configuration
Loads settings from environment variables (.env file).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

from i18n import APP_LANG, t  # "id" | "en" (from APP_LANG env)

load_dotenv()


# -- Telegram Bot (admin) ------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_IDS = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if uid.strip().lstrip("-").isdigit()
]

# -- WhatsApp Provider ----------------------------------------
WA_PROVIDER = os.getenv("WA_PROVIDER", "twilio").lower()  # twilio | meta | gowa

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")

# Meta Cloud API
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")
WA_BASE_URL = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/messages"

# GoWA (self-hosted, e.g. https://whatsapp.masyamin.biz.id)
GOWA_BASE_URL = os.getenv("GOWA_BASE_URL", "").rstrip("/")
GOWA_USERNAME = os.getenv("GOWA_USERNAME", "")
GOWA_PASSWORD = os.getenv("GOWA_PASSWORD", "")
GOWA_DEVICE_ID = os.getenv("GOWA_DEVICE_ID", "")  # optional X-Device-Id header
try:
    GOWA_SEND_INTERVAL_SECONDS = float(os.getenv("GOWA_SEND_INTERVAL_SECONDS", "15"))
except ValueError:
    GOWA_SEND_INTERVAL_SECONDS = 15.0
if GOWA_SEND_INTERVAL_SECONDS < 1:
    GOWA_SEND_INTERVAL_SECONDS = 1.0
# Random anti-ban delay range (seconds): each message waits random.uniform(MIN, MAX)
try:
    GOWA_DELAY_MIN_SECONDS = float(os.getenv("GOWA_DELAY_MIN_SECONDS", "15"))
except ValueError:
    GOWA_DELAY_MIN_SECONDS = 15.0
try:
    GOWA_DELAY_MAX_SECONDS = float(os.getenv("GOWA_DELAY_MAX_SECONDS", "30"))
except ValueError:
    GOWA_DELAY_MAX_SECONDS = 30.0
if GOWA_DELAY_MIN_SECONDS < 1:
    GOWA_DELAY_MIN_SECONDS = 1.0
if GOWA_DELAY_MAX_SECONDS < GOWA_DELAY_MIN_SECONDS:
    GOWA_DELAY_MAX_SECONDS = GOWA_DELAY_MIN_SECONDS

# -- Webhook Server -------------------------------------------
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
try:
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
except ValueError:
    WEBHOOK_PORT = 8000

# -- Database -------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "whatsapp_sender.db")

# -- Rate Limiting --------------------------------------------
try:
    WA_MESSAGES_PER_SECOND = int(os.getenv("WA_MESSAGES_PER_SECOND", "50"))
except ValueError:
    WA_MESSAGES_PER_SECOND = 50

# -- Bot Identity ---------------------------------------------
BOT_NAME = "WhatsApp Bulk Sender"
BOT_VERSION = "2.0.0"
COMMUNITY_URL = "https://github.com/SoCloseSociety"
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://soclose.co")

# -- Dashboard ------------------------------------------------
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or DASHBOARD_PASSWORD or ""


def validate(lang: str | None = None) -> list[str]:
    """Return a list of configuration warnings (in the selected language)."""
    lang = (lang or APP_LANG).lower()
    warnings = []

    if WA_PROVIDER == "twilio":
        if not TWILIO_ACCOUNT_SID:
            warnings.append(t("cfgw.twilio_sid", lang))
        if not TWILIO_AUTH_TOKEN:
            warnings.append(t("cfgw.twilio_token", lang))
        if not TWILIO_WHATSAPP_FROM:
            warnings.append(t("cfgw.twilio_from", lang))
    elif WA_PROVIDER == "meta":
        if not WA_PHONE_NUMBER_ID:
            warnings.append(t("cfgw.meta_phone_id", lang))
        if not WA_ACCESS_TOKEN:
            warnings.append(t("cfgw.meta_token", lang))
    elif WA_PROVIDER == "gowa":
        if not GOWA_BASE_URL:
            warnings.append(t("cfgw.gowa_url", lang))
        if not GOWA_USERNAME or not GOWA_PASSWORD:
            warnings.append(t("cfgw.gowa_auth", lang))
    else:
        warnings.append(t("cfgw.unknown_provider", lang).format(p=WA_PROVIDER))

    if WA_MESSAGES_PER_SECOND <= 0:
        warnings.append(t("cfgw.rate", lang).format(v=WA_MESSAGES_PER_SECOND))

    return warnings

# v2.0: multi-user + anti-ban + scheduled messages
