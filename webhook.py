"""
WhatsApp Bulk Sender — Webhook Server
FastAPI endpoints for WhatsApp delivery status callbacks and
GoWA incoming-message webhooks (Balasan Masuk).
Handles both Meta Cloud API and Twilio status updates.
"""

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

import config
import database

logger = logging.getLogger(__name__)

# Valid delivery statuses (anything else is rejected)
_VALID_STATUSES = {"queued", "sent", "delivered", "read", "failed"}

# -- Lifespan -------------------------------------------------

_initialized = False


def mark_initialized():
    """Called by main.py to prevent double-init in telegram mode."""
    global _initialized
    _initialized = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _initialized
    if not _initialized:
        await database.init()
        _initialized = True
        logger.info(f"{config.BOT_NAME} v{config.BOT_VERSION} webhook server started")
    yield


app = FastAPI(title=config.BOT_NAME, version=config.BOT_VERSION, lifespan=lifespan)


# -- Meta Cloud API Webhook ------------------------------------

@app.get("/webhook")
async def meta_verify(request: Request):
    """Meta webhook verification (challenge-response)."""
    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and config.WA_VERIFY_TOKEN and hmac.compare_digest(token, config.WA_VERIFY_TOKEN):
        logger.info("Meta webhook verified")
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook")
async def meta_webhook(request: Request):
    """Receive Meta Cloud API delivery status updates."""
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook JSON: {e}")
        return {"status": "error"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Status updates (delivery receipts)
            for status_update in value.get("statuses", []):
                sid = status_update.get("id", "")
                new_status = status_update.get("status", "")
                if sid and new_status in _VALID_STATUSES:
                    await database.update_message_status(sid, new_status)
                    logger.debug(f"Status update: {sid} -> {new_status}")
                elif new_status:
                    logger.warning(f"Unknown status '{new_status}' for {sid}, ignoring")

    return {"status": "ok"}


# -- Twilio Status Callback ------------------------------------

@app.post("/twilio-status")
async def twilio_status(request: Request):
    """Handle Twilio delivery status callbacks."""
    form = await request.form()
    sid = str(form.get("MessageSid", ""))
    status = str(form.get("MessageStatus", ""))

    # Normalize Twilio status to match our naming
    status_map = {
        "queued": "queued",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "undelivered": "failed",
        "failed": "failed",
    }
    normalized = status_map.get(status)

    if sid and normalized:
        await database.update_message_status(sid, normalized)

    return {"status": "ok"}


# -- GoWA Incoming Messages (Balasan Masuk) ----------------------


def _verify_gowa_signature(raw_body: bytes, signature: str) -> bool:
    """Verify GoWA X-Hub-Signature-256 header (HMAC-SHA256 over raw body).

    The secret must match --webhook-secret / WHATSAPP_WEBHOOK_SECRET on the
    GoWA server. Fail closed: missing secret or signature => reject.
    """
    secret = config.GOWA_WEBHOOK_SECRET or ""
    if not secret or not signature:
        return False
    received = signature[7:] if signature.startswith("sha256=") else signature
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, received)
    except Exception:
        return False


def _normalize_gowa_phone(jid: str) -> str | None:
    """Extract digits from a WhatsApp JID and normalize to app format (E.164)."""
    if not jid:
        return None
    user_part = str(jid).split("@", 1)[0]
    digits = "".join(c for c in user_part if c.isdigit())
    if not digits or len(digits) < 8:
        return None
    try:
        import csv_handler
        return csv_handler.normalize_phone(digits) or f"+{digits}"
    except Exception:
        return f"+{digits}"


async def _handle_gowa_event(body: dict) -> None:
    """Process one GoWA webhook event. Stores incoming text replies; never raises."""
    if not isinstance(body, dict):
        return
    event = str(body.get("event", "")).lower()
    if event != "message":
        return  # ignore reactions, acks, presence, calls, etc.

    # session_id = the GoWA device slot id (e.g. wasend-username)
    session_id = str(body.get("session_id") or "")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return

    if payload.get("is_from_me"):
        return  # ignore our own outgoing messages
    text = payload.get("body")
    if not isinstance(text, str) or not text.strip():
        return  # media/location/etc. without text — skip

    phone = _normalize_gowa_phone(payload.get("from") or "")
    if not phone:
        logger.warning("GoWA webhook: could not parse sender JID %r",
                       payload.get("from"))
        return

    user_id = await database.get_user_id_by_device(session_id)
    if user_id is None:
        logger.warning("GoWA webhook: no wasend user for device session %r, "
                       "reply from %s dropped", session_id, phone)
        return

    is_stop = text.strip().upper() == "STOP"
    await database.log_incoming_reply(
        user_id=user_id,
        device_id=session_id,
        phone=phone,
        message_text=text.strip(),
        is_stop=is_stop,
    )
    logger.info("GoWA webhook: stored reply from %s (stop=%s)", phone, is_stop)

    if is_stop:
        added = await database.add_blocked(user_id, phone, reason="STOP (webhook)")
        logger.info("GoWA webhook: STOP from %s -> blocklist %s",
                    phone, "added" if added else "already present")


@app.post("/gowa-incoming")
async def gowa_incoming(request: Request):
    """Receive GoWA incoming-message webhooks.

    Verifies the HMAC signature, stores every incoming text reply in the
    database, and auto-adds STOP replies to the owning user's blocklist.
    Always answers quickly and never raises on bad payloads.
    """
    raw = await request.body()
    if not _verify_gowa_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("GoWA webhook: bad or missing signature, ignoring")
        return {"status": "ignored"}

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as e:
        logger.error(f"GoWA webhook: failed to parse JSON: {e}")
        return {"status": "ok"}

    try:
        await _handle_gowa_event(body)
    except Exception as e:
        logger.error(f"GoWA webhook: handler error: {e}")
    return {"status": "ok"}


# -- Health Check ----------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "bot": config.BOT_NAME,
        "version": config.BOT_VERSION,
        "provider": config.WA_PROVIDER,
    }


@app.get("/")
async def root():
    return {
        "name": config.BOT_NAME,
        "version": config.BOT_VERSION,
        "endpoints": {
            "health": "/health",
            "webhook_meta": "/webhook",
            "twilio_status": "/twilio-status",
            "gowa_incoming": "/gowa-incoming",
            "docs": "/docs",
        },
    }
