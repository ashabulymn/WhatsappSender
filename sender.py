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
from typing import Callable

import config
import database
from whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

_SPINTAX_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
_SLOT_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


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
) -> dict:
    """
    Send a campaign with anti-ban safeguards.

    - Each recipient receives exactly ONE randomly picked variant (weighted).
    - Random delay uniform(delay_min, delay_max) between messages.
    - Every send is logged with variant_id for audit.
    """
    delay_min = config.GOWA_DELAY_MIN_SECONDS if delay_min is None else max(0.5, delay_min)
    delay_max = config.GOWA_DELAY_MAX_SECONDS if delay_max is None else max(delay_min, delay_max)
    if variants is None:
        variants = await database.get_variants(user_id, campaign_id)

    contacts = await database.get_contacts_for_campaign(campaign_id)
    total = len(contacts)
    if not total:
        return {"sent": 0, "failed": 0, "total": 0, "results": []}

    wa = WhatsAppClient(device_id=device_id)
    rng = random.Random()
    results = {"sent": 0, "failed": 0, "total": total, "results": []}

    for i, contact in enumerate(contacts):
        phone = contact.get("phone") or ""
        variant = pick_variant(variants, rng)
        if variant:
            body = render_message(variant["body"], contact, rng)
            variant_id = variant["id"]
        else:
            campaign = await database.get_campaign(campaign_id)
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
        results["results"].append({"phone": phone, **result})

        if on_progress:
            try:
                on_progress(i + 1, total, phone)
            except Exception:
                pass

        if i < total - 1:
            delay = rng.uniform(delay_min, delay_max)
            logger.info(f"Anti-ban delay {delay:.1f}s before next message ({i+1}/{total})")
            await asyncio.sleep(delay)

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
