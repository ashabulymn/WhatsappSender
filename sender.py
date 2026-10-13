"""
Wasend — Anti-ban sending engine.

- Random message variant per recipient (weighted), each recipient gets exactly ONE variant
- Spintax: {Halo|Hai|Assalamu'alaikum} -> one random alternative per send
- Slots: {first_name}, {last_name}, {phone}, {name} from contact data
- Random delay between messages: uniform(delay_min, delay_max) seconds
- Per-user GoWA device (X-Device-Id)
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import time
from typing import Callable

import config
import database
from whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

_SPINTAX_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
_SLOT_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# -- Blast play/pause/stop controls ------------------------------
# Per-campaign control registry, shared between the dashboard UI thread
# and the background blast thread (or the scheduler thread).

_BLAST_CONTROLS: dict[int, dict] = {}
_BLAST_LOCK = threading.Lock()


def get_blast_control(campaign_id: int) -> dict:
    """Return (creating if needed) the play/pause/stop control for a campaign."""
    with _BLAST_LOCK:
        return _BLAST_CONTROLS.setdefault(campaign_id, {
            "pause": threading.Event(),
            "stop": threading.Event(),
            "sent": 0,
            "failed": 0,
            "total": 0,
            "status": "idle",  # idle|running|paused|stopped|done|error
            "stop_reason": "",
            "started_at": 0.0,
        })


def reset_blast_control(campaign_id: int) -> dict:
    with _BLAST_LOCK:
        ctrl = _BLAST_CONTROLS.get(campaign_id)
        if ctrl:
            ctrl["pause"].clear()
            ctrl["stop"].clear()
            ctrl.update(sent=0, failed=0, total=0, status="idle",
                        stop_reason="", started_at=0.0)
            return ctrl
        return get_blast_control(campaign_id)


async def _interruptible_sleep(delay: float, ctrl: dict) -> str:
    """Sleep in small chunks; returns 'stopped' if stop was requested."""
    end = time.time() + delay
    while time.time() < end:
        if ctrl["stop"].is_set():
            return "stopped"
        while ctrl["pause"].is_set():
            ctrl["status"] = "paused"
            await asyncio.sleep(1)
            if ctrl["stop"].is_set():
                return "stopped"
        ctrl["status"] = "running"
        await asyncio.sleep(min(1.0, max(0.1, end - time.time())))
    return "ok"


def render_spintax(text: str, rng: random.Random | None = None) -> str:
    """Replace {a|b|c} with one random alternative. Nested-safe via loop."""
    rng = rng or random
    def _pick(m: re.Match) -> str:
        options = [o.strip() for o in m.group(1).split("|")]
        options = [o for o in options if o]
        return rng.choice(options) if options else m.group(0)
    prev = None
    while prev != text:
        prev = text
        text = _SPINTAX_RE.sub(_pick, text)
    return text


def render_slots(text: str, contact: dict) -> str:
    """Replace {first_name} etc. from contact data (spintax alternatives untouched)."""
    first_name = contact.get("first_name") or "Contact"
    last_name = contact.get("last_name") or ""
    def _slot(m: re.Match) -> str:
        key = m.group(1)
        if "|" in m.group(0):
            return m.group(0)  # spintax, handled later
        return {
            "first_name": first_name,
            "last_name": last_name,
            "phone": contact.get("phone") or "",
            "name": f"{first_name} {last_name}".strip(),
        }.get(key, m.group(0))
    return _SLOT_RE.sub(_slot, text)


def render_message(body: str, contact: dict, rng: random.Random | None = None) -> str:
    """Full render: slots first, then spintax."""
    return render_spintax(render_slots(body, contact), rng)


def pick_variant(variants: list[dict], rng: random.Random | None = None) -> dict | None:
    """Weighted random pick. Returns None if no variants."""
    rng = rng or random
    if not variants:
        return None
    weights = [max(1, int(v.get("weight") or 1)) for v in variants]
    return rng.choices(variants, weights=weights, k=1)[0]


async def send_campaign(
    user_id: int,
    campaign_id: int,
    variants: list[dict] | None = None,
    delay_min: float | None = None,
    delay_max: float | None = None,
    device_id: str = "",
    on_progress: Callable[[int, int, str], None] | None = None,
    max_duration_min: float | None = None,
    max_messages: int | None = None,
    only_phones: list[str] | None = None,
) -> dict:
    """
    Send a campaign with anti-ban safeguards.

    - Each recipient receives exactly ONE randomly picked variant (weighted).
    - Random delay uniform(delay_min, delay_max) between messages.
    - Every send is logged with variant_id for audit.
    - Play/pause/stop via get_blast_control(campaign_id).
    - Optional limits: max_duration_min (0 = unlimited), max_messages (0 = unlimited).
      None values fall back to the campaign row.
    """
    delay_min = config.GOWA_DELAY_MIN_SECONDS if delay_min is None else max(0.5, delay_min)
    delay_max = config.GOWA_DELAY_MAX_SECONDS if delay_max is None else max(delay_min, delay_max)
    if variants is None:
        variants = await database.get_variants(user_id, campaign_id)
    campaign = await database.get_campaign(campaign_id) or {}
    if max_duration_min is None:
        max_duration_min = campaign.get("max_duration_min") or 0
    if max_messages is None:
        max_messages = campaign.get("max_messages") or 0

    contacts = await database.get_contacts_for_campaign(campaign_id)
    # retry subset
    if only_phones is not None:
        _want = set(only_phones)
        contacts = [c for c in contacts if c.get("phone") in _want]
    # blocklist (opt-out) — never send to blocked numbers
    try:
        _blocked = await database.get_blocked_phones(user_id)
    except Exception:
        _blocked = set()
    if _blocked:
        contacts = [c for c in contacts if c.get("phone") not in _blocked]
    total = len(contacts)
    results = {"sent": 0, "failed": 0, "total": total, "results": [],
               "stopped_early": False, "stop_reason": ""}
    if not total:
        return results

    ctrl = get_blast_control(campaign_id)
    if ctrl["status"] in ("running", "paused"):
        results["stopped_early"] = True
        results["stop_reason"] = "already_running"
        return results
    ctrl["pause"].clear()
    ctrl["stop"].clear()
    ctrl.update(status="running", sent=0, failed=0, total=total,
                stop_reason="", started_at=time.time())
    start_ts = time.time()

    wa = WhatsAppClient(device_id=device_id)
    rng = random.Random()

    for i, contact in enumerate(contacts):
        if ctrl["stop"].is_set():
            results["stopped_early"] = True
            results["stop_reason"] = "manual"
            break
        while ctrl["pause"].is_set():
            ctrl["status"] = "paused"
            await asyncio.sleep(1)
            if ctrl["stop"].is_set():
                break
        if ctrl["stop"].is_set():
            results["stopped_early"] = True
            results["stop_reason"] = "manual"
            break
        ctrl["status"] = "running"

        # Limit checks (before sending)
        if max_messages and results["sent"] >= max_messages:
            results["stopped_early"] = True
            results["stop_reason"] = "limit_messages"
            break
        if max_duration_min and (time.time() - start_ts) > max_duration_min * 60:
            results["stopped_early"] = True
            results["stop_reason"] = "limit_time"
            break

        phone = contact.get("phone") or ""
        variant = pick_variant(variants, rng)
        if variant:
            body = render_message(variant["body"], contact, rng)
            variant_id = variant["id"]
        else:
            body = render_message(campaign.get("message") or "", contact, rng)
            variant_id = None

        result = await wa.send_message(phone, body)
        await database.log_message(
            phone=phone,
            content=body[:500],
            status=result["status"],
            campaign_id=campaign_id,
            contact_id=contact.get("id"),
            wa_message_id=result.get("sid", ""),
            error_message=result.get("error", ""),
            user_id=user_id,
            variant_id=variant_id,
        )
        if result["status"] == "sent":
            results["sent"] += 1
        else:
            results["failed"] += 1
        ctrl["sent"] = results["sent"]
        ctrl["failed"] = results["failed"]
        results["results"].append({"phone": phone, **result})

        if on_progress:
            try:
                on_progress(i + 1, total, phone)
            except Exception:
                pass

        if i < total - 1:
            delay = rng.uniform(delay_min, delay_max)
            logger.info(f"Anti-ban delay {delay:.1f}s before next message ({i+1}/{total})")
            if await _interruptible_sleep(delay, ctrl) == "stopped":
                results["stopped_early"] = True
                results["stop_reason"] = "manual"
                break

    if results["stopped_early"]:
        ctrl["status"] = "stopped"
        ctrl["stop_reason"] = results["stop_reason"]
        await database.update_campaign_status(campaign_id, "stopped")
    else:
        ctrl["status"] = "done"
        await database.update_campaign_status(campaign_id, "sent")
    return results


async def send_single(
    user_id: int,
    phone: str,
    body: str,
    device_id: str = "",
    contact: dict | None = None,
    campaign_id: int | None = None,
) -> dict:
    """Send one message (with spintax/slot rendering). Used by test + scheduler."""
    contact = contact or {"phone": phone, "first_name": "Contact", "last_name": ""}
    rendered = render_message(body, contact)
    wa = WhatsAppClient(device_id=device_id)
    result = await wa.send_message(phone, rendered)
    await database.log_message(
        phone=phone,
        content=rendered[:500],
        status=result["status"],
        campaign_id=campaign_id,
        wa_message_id=result.get("sid", ""),
        error_message=result.get("error", ""),
        user_id=user_id,
    )
    return {"rendered": rendered, **result}
