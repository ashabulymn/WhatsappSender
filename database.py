"""
WhatsApp Bulk Sender — Database Layer
SQLite async database for users, contacts, campaigns, message logs, and templates.
Multi-user: every data table carries user_id; admins can see all.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import logging
import os
import secrets

import aiosqlite
from datetime import datetime

import config

logger = logging.getLogger(__name__)

_DB = config.DB_PATH

# -- Schema ---------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    is_admin        INTEGER DEFAULT 0,
    gowa_device_id  TEXT DEFAULT '',
    device_name     TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    first_name  TEXT DEFAULT 'Contact',
    last_name   TEXT DEFAULT '',
    phone       TEXT NOT NULL,
    email       TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, phone)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL,
    message     TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',
    created_at  TEXT DEFAULT (datetime('now')),
    sent_at     TEXT
);

CREATE TABLE IF NOT EXISTS campaign_contacts (
    campaign_id INTEGER NOT NULL,
    contact_id  INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, contact_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

CREATE TABLE IF NOT EXISTS message_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL DEFAULT 1,
    contact_id      INTEGER,
    campaign_id     INTEGER,
    phone           TEXT NOT NULL,
    content         TEXT DEFAULT '',
    status          TEXT DEFAULT 'queued',
    wa_message_id   TEXT DEFAULT '',
    error_message   TEXT DEFAULT '',
    sent_at         TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE SET NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    body        TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
"""

# Migration pieces for databases created before multi-user (per-table, idempotent)
_MIGRATE_CONTACTS = """
PRAGMA foreign_keys=OFF;
CREATE TABLE IF NOT EXISTS contacts_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    first_name  TEXT DEFAULT 'Contact',
    last_name   TEXT DEFAULT '',
    phone       TEXT NOT NULL,
    email       TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, phone)
);
INSERT OR IGNORE INTO contacts_new (id, user_id, first_name, last_name, phone, email, created_at)
    SELECT id, 1, first_name, last_name, phone, email, created_at FROM contacts;
DROP TABLE contacts;
ALTER TABLE contacts_new RENAME TO contacts;
PRAGMA foreign_keys=ON;
"""

_MIGRATE_TEMPLATES = """
PRAGMA foreign_keys=OFF;
CREATE TABLE IF NOT EXISTS templates_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    body        TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
INSERT OR IGNORE INTO templates_new (id, user_id, name, category, body, created_at)
    SELECT id, 1, name, category, body, created_at FROM templates;
DROP TABLE templates;
ALTER TABLE templates_new RENAME TO templates;
PRAGMA foreign_keys=ON;
"""

_DEFAULT_TEMPLATES = [
    (
        "simple_hello",
        "general",
        "Halo {first_name}! Ini pesan dari kami. Ada yang bisa kami bantu?",
    ),
    (
        "promo",
        "broadcast",
        (
            "Halo {first_name}!\n\n"
            "Jangan lewatkan penawaran spesial kami bulan ini.\n"
            "Balas pesan ini untuk info lebih lanjut.\n\n"
            "Terima kasih!"
        ),
    ),
    (
        "reminder",
        "broadcast",
        "Halo {first_name}, ini pengingat dari kami. Jangan ragu untuk menghubungi kami kembali!",
    ),
]

# Old French default bodies (replaced one-time on init if still untouched)
_FRENCH_DEFAULT_BODIES = {
    "simple_hello": "Bonjour {first_name} ! Ceci est un message de la part de SoClose Society.",
    "promo": (
        "Salut {first_name} !\n\n"
        "Decouvrez nos derniers outils et projets open-source.\n"
        "Plus d'infos sur https://soclose.co\n\n"
        "A bientot !"
    ),
    "reminder": "Bonjour {first_name}, un petit rappel de SoClose Society. N'hesitez pas a nous contacter !",
}


# -- Connection helper ----------------------------------------

async def _connect() -> aiosqlite.Connection:
    """Open a connection with WAL mode and foreign keys enabled."""
    db = await aiosqlite.connect(_DB)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


# -- Init -----------------------------------------------------

async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _migrate_multiuser(db: aiosqlite.Connection):
    """Upgrade pre-multi-user databases: add users table, user_id columns,
    and convert phone/name uniques to per-user. Idempotent and safe on
    partial databases (each table migrated only if it exists and lacks user_id)."""
    for table, script in (("contacts", _MIGRATE_CONTACTS),
                          ("templates", _MIGRATE_TEMPLATES)):
        cols = await _table_columns(db, table)
        if cols and "user_id" not in cols:
            logger.info("Migrating table '%s' to multi-user schema...", table)
            await db.executescript(script)
            await db.commit()
    for table in ("campaigns", "message_log"):
        cols = await _table_columns(db, table)
        if cols and "user_id" not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            await db.commit()
            logger.info("Added user_id to %s", table)


async def init():
    """Create tables, migrate old DBs, seed admin + default templates."""
    db = await _connect()
    try:
        await db.executescript(_SCHEMA)
        await _migrate_multiuser(db)
        await _ensure_extra_tables(db)
        admin_id = await ensure_admin(db)
        # Bootstrap: link admin to an existing GoWA device via env (one-time)
        admin_device_id = (os.getenv("ADMIN_DEVICE_ID") or "").strip()
        if admin_device_id:
            existing = await get_user_devices(admin_id, _db=db)
            if not any(d["gowa_device_id"] == admin_device_id for d in existing):
                dname = ""
                try:
                    from whatsapp import gowa_device_info as _gdi
                    info = _gdi(admin_device_id)
                    if info.get("ok"):
                        dname = info.get("display_name") or info.get("name", "")
                except Exception as e:
                    logger.warning("ADMIN_DEVICE_ID lookup failed: %s", e)
                await db.execute(
                    "INSERT OR IGNORE INTO user_devices (user_id, gowa_device_id, device_name, is_default)"
                    " VALUES (?, ?, ?, ?)",
                    (admin_id, admin_device_id, dname, 0 if existing else 1),
                )
                await db.commit()
                logger.info("Linked admin to GoWA device %s", admin_device_id)
        for name, category, body in _DEFAULT_TEMPLATES:
            await db.execute(
                """INSERT OR IGNORE INTO templates (user_id, name, category, body)
                   VALUES (?, ?, ?, ?)""",
                (admin_id, name, category, body),
            )
        # One-time repair: replace untouched French default bodies with Indonesian
        for name, category, new_body in _DEFAULT_TEMPLATES:
            old_body = _FRENCH_DEFAULT_BODIES.get(name)
            if old_body:
                await db.execute(
                    "UPDATE templates SET body = ? WHERE name = ? AND body = ?",
                    (new_body, name, old_body),
                )
        await db.commit()
    finally:
        await db.close()


# -- Users ----------------------------------------------------

def hash_password(password: str) -> str:
    """PBKDF2-SHA256 password hash (stdlib only)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"pbkdf2$200000${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        prefix, rounds, salt, hash_hex = stored.split("$")
        if prefix != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt), int(rounds))
        return hmac_lib.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


async def ensure_admin(db: aiosqlite.Connection | None = None) -> int:
    """Create the admin user from env on first run. Returns admin user id.

    Desired-state: if ADMIN_USERNAME is set and the database has exactly one
    user (the bootstrap admin) with a different username, rename it — so the
    login always matches what the owner configured, regardless of deploy order.
    """
    own = db is None
    if own:
        db = await _connect()
    try:
        cursor = await db.execute("SELECT id, username FROM users WHERE is_admin = 1 LIMIT 1")
        row = await cursor.fetchone()
        want_username = (os.getenv("ADMIN_USERNAME") or "admin").strip() or "admin"
        if row:
            admin_id, current_username = row[0], row[1]
            if want_username != current_username:
                cursor = await db.execute("SELECT COUNT(*) FROM users")
                total = (await cursor.fetchone())[0]
                if total == 1:
                    await db.execute("UPDATE users SET username = ? WHERE id = ?",
                                     (want_username, admin_id))
                    await db.commit()
                    logger.info("Renamed bootstrap admin '%s' -> '%s'",
                                current_username, want_username)
            return admin_id
        password = os.getenv("ADMIN_PASSWORD") or config.DASHBOARD_PASSWORD or ""
        if not password:
            password = secrets.token_urlsafe(16)
            logger.warning("ADMIN_PASSWORD/DASHBOARD_PASSWORD not set — generated random admin password: %s", password)
        cursor = await db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            (want_username, hash_password(password)),
        )
        await db.commit()
        logger.info("Created admin user '%s' (id %s)", want_username, cursor.lastrowid)
        return cursor.lastrowid or 1
    finally:
        if own:
            await db.close()


async def create_user(username: str, password: str, is_admin: bool = False) -> int | None:
    """Create a user. Returns id, or None if username taken."""
    db = await _connect()
    try:
        cursor = await db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username.strip(), hash_password(password), 1 if is_admin else 0),
        )
        await db.commit()
        return cursor.lastrowid
    except Exception:
        return None
    finally:
        await db.close()


async def get_user_by_username(username: str) -> dict | None:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_claimed_device_ids() -> set[str]:
    """All gowa_device_id currently claimed by wasend users."""
    rows = await get_all_device_rows()
    return {r["gowa_device_id"] for r in rows}


# -- Multi-device (up to DEVICE_MAX_PER_USER per user) ---------

MAX_DEVICES_PER_USER = int(os.getenv("DEVICE_MAX_PER_USER", "3") or 3)


async def get_user_devices(user_id: int, _db=None) -> list[dict]:
    own = _db is None
    db = _db or await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_devices WHERE user_id = ? ORDER BY is_default DESC, id",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        if own:
            await db.close()


async def get_default_device(user_id: int) -> dict | None:
    devs = await get_user_devices(user_id)
    return devs[0] if devs else None


async def add_user_device(user_id: int, gowa_device_id: str, device_name: str = "") -> dict:
    """Claim a GoWA slot for a user. Raises ValueError('max_devices') at the limit."""
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_devices WHERE user_id = ?", (user_id,))
        count = (await cursor.fetchone())[0]
        if count >= MAX_DEVICES_PER_USER:
            raise ValueError("max_devices")
        cursor = await db.execute(
            """INSERT OR IGNORE INTO user_devices (user_id, gowa_device_id, device_name, is_default)
               VALUES (?, ?, ?, ?)""",
            (user_id, gowa_device_id, device_name or "", 1 if count == 0 else 0),
        )
        await db.commit()
        devs = await get_user_devices(user_id, _db=db)
        return next(d for d in devs if d["gowa_device_id"] == gowa_device_id)
    finally:
        await db.close()


async def remove_user_device(user_id: int, gowa_device_id: str) -> None:
    db = await _connect()
    try:
        await db.execute(
            "DELETE FROM user_devices WHERE user_id = ? AND gowa_device_id = ?",
            (user_id, gowa_device_id),
        )
        # Promote another device to default if needed
        cursor = await db.execute(
            "SELECT COUNT(*) FROM user_devices WHERE user_id = ? AND is_default = 1",
            (user_id,),
        )
        if (await cursor.fetchone())[0] == 0:
            await db.execute(
                """UPDATE user_devices SET is_default = 1 WHERE id = (
                       SELECT id FROM user_devices WHERE user_id = ? ORDER BY id LIMIT 1)""",
                (user_id,),
            )
        await db.commit()
    finally:
        await db.close()


async def set_default_device(user_id: int, gowa_device_id: str) -> None:
    db = await _connect()
    try:
        await db.execute("UPDATE user_devices SET is_default = 0 WHERE user_id = ?", (user_id,))
        await db.execute(
            "UPDATE user_devices SET is_default = 1 WHERE user_id = ? AND gowa_device_id = ?",
            (user_id, gowa_device_id),
        )
        await db.commit()
    finally:
        await db.close()


async def touch_device_login(gowa_device_id: str) -> None:
    """Mark a device as seen logged-in (used by the auto-cleanup janitor)."""
    db = await _connect()
    try:
        await db.execute(
            "UPDATE user_devices SET last_logged_in_at = datetime('now') WHERE gowa_device_id = ?",
            (gowa_device_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_device_rows() -> list[dict]:
    """Every claimed device with owner info (for janitor + admin views)."""
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT d.*, u.username FROM user_devices d
               JOIN users u ON u.id = d.user_id ORDER BY d.user_id, d.id"""
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_admin_user() -> dict | None:
    """Return the first admin user (used by CLI / Telegram bot single-user mode)."""
    users = await get_all_users()
    for u in users:
        if u["is_admin"]:
            return await get_user(u["id"])
    return None


async def get_user(user_id: int) -> dict | None:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_users() -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT u.id, u.username, u.is_admin, u.gowa_device_id, u.device_name, u.created_at,
                      (SELECT COUNT(*) FROM user_devices d WHERE d.user_id = u.id) AS device_count
               FROM users u ORDER BY u.id"""
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def delete_user(user_id: int) -> bool:
    """Delete a user and all their data. Returns False if user is admin."""
    db = await _connect()
    try:
        cursor = await db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row or row[0]:
            return False
        await db.execute("DELETE FROM campaign_contacts WHERE campaign_id IN (SELECT id FROM campaigns WHERE user_id = ?)", (user_id,))
        await db.execute("DELETE FROM message_log WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_devices WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM message_variants WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM scheduled_messages WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM campaigns WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM templates WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
        return True
    finally:
        await db.close()


async def update_user_password(user_id: int, password: str) -> None:
    db = await _connect()
    try:
        await db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (hash_password(password), user_id))
        await db.commit()
    finally:
        await db.close()




# -- Contacts -------------------------------------------------

def _user_filter(user_id: int | None, alias: str = "") -> tuple[str, tuple]:
    """SQL filter fragment for per-user isolation. None = admin sees all."""
    prefix = f"{alias}." if alias else ""
    if user_id is None:
        return "", ()
    return f"WHERE {prefix}user_id = ?", (user_id,)


async def upsert_contact(first_name: str, last_name: str, phone: str,
                         email: str = "", user_id: int = 1) -> int:
    """Create or update a contact (per user). Returns contact ID."""
    db = await _connect()
    try:
        cursor = await db.execute(
            """INSERT INTO contacts (user_id, first_name, last_name, phone, email)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, phone) DO UPDATE SET
                 first_name = CASE WHEN excluded.first_name != 'Contact' THEN excluded.first_name ELSE contacts.first_name END,
                 last_name = CASE WHEN excluded.last_name != '' THEN excluded.last_name ELSE contacts.last_name END,
                 email = CASE WHEN excluded.email != '' THEN excluded.email ELSE contacts.email END""",
            (user_id, first_name or "Contact", last_name or "", phone, email or ""),
        )
        await db.commit()
        row_cursor = await db.execute(
            "SELECT id FROM contacts WHERE user_id = ? AND phone = ?", (user_id, phone))
        row = await row_cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


async def get_contact_by_phone(phone: str, user_id: int | None = None) -> dict | None:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        where = where + (" AND " if where else "WHERE ") + "phone = ?"
        cursor = await db.execute(f"SELECT * FROM contacts {where}", (*params, phone))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_contacts(user_id: int | None = None) -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        cursor = await db.execute(
            f"SELECT * FROM contacts {where} ORDER BY created_at DESC", params)
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_contact_count(user_id: int | None = None) -> int:
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        cursor = await db.execute(f"SELECT COUNT(*) FROM contacts {where}", params)
        row = await cursor.fetchone()
        return row[0]
    finally:
        await db.close()


async def delete_all_contacts(user_id: int | None = None) -> int:
    """Delete contacts (per user, or all for admin). Nullifies message_log refs."""
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        cursor = await db.execute(f"SELECT COUNT(*) FROM contacts {where}", params)
        row = await cursor.fetchone()
        count = row[0]
        if user_id is None:
            await db.execute("UPDATE message_log SET contact_id = NULL")
            await db.execute("DELETE FROM campaign_contacts")
            await db.execute("DELETE FROM contacts")
        else:
            ids = [r[0] for r in await (await db.execute(
                "SELECT id FROM contacts WHERE user_id = ?", (user_id,))).fetchall()]
            if ids:
                q = ",".join("?" * len(ids))
                await db.execute(f"UPDATE message_log SET contact_id = NULL WHERE contact_id IN ({q})", ids)
                await db.execute(f"DELETE FROM campaign_contacts WHERE contact_id IN ({q})", ids)
            await db.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
        await db.commit()
        return count
    finally:
        await db.close()


# -- Campaigns ------------------------------------------------

async def create_campaign(name: str, message: str = "", user_id: int = 1) -> int:
    db = await _connect()
    try:
        cursor = await db.execute(
            "INSERT INTO campaigns (user_id, name, message) VALUES (?, ?, ?)",
            (user_id, name, message),
        )
        await db.commit()
        return cursor.lastrowid or 0
    finally:
        await db.close()


async def get_campaign(campaign_id: int) -> dict | None:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def update_campaign_status(campaign_id: int, status: str) -> None:
    db = await _connect()
    try:
        if status == "sent":
            await db.execute(
                "UPDATE campaigns SET status = ?, sent_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), campaign_id),
            )
        else:
            await db.execute(
                "UPDATE campaigns SET status = ? WHERE id = ?",
                (status, campaign_id),
            )
        await db.commit()
    finally:
        await db.close()


async def update_campaign_limits(campaign_id: int, max_duration_min: float,
                                 max_messages: int) -> None:
    """Save blast limits on a campaign (0 = unlimited)."""
    db = await _connect()
    try:
        await db.execute(
            "UPDATE campaigns SET max_duration_min = ?, max_messages = ? WHERE id = ?",
            (max(0, max_duration_min or 0), max(0, int(max_messages or 0)), campaign_id),
        )
        await db.commit()
    finally:
        await db.close()


async def set_campaign_rotation(campaign_id: int, on: bool) -> None:
    """Persist the device-rotation flag on a campaign."""
    db = await _connect()
    try:
        await db.execute("UPDATE campaigns SET rotate_devices = ? WHERE id = ?",
                         (1 if on else 0, campaign_id))
        await db.commit()
    finally:
        await db.close()


async def delete_campaign(campaign_id: int) -> None:
    """Delete a campaign and its links. Keeps message_log for audit (campaign_id nulled)."""
    db = await _connect()
    try:
        await db.execute("DELETE FROM campaign_contacts WHERE campaign_id = ?", (campaign_id,))
        await db.execute("DELETE FROM message_variants WHERE campaign_id = ?", (campaign_id,))
        await db.execute("DELETE FROM scheduled_messages WHERE campaign_id = ?", (campaign_id,))
        await db.execute("UPDATE message_log SET campaign_id = NULL WHERE campaign_id = ?",
                         (campaign_id,))
        await db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        await db.commit()
    finally:
        await db.close()


async def update_contact(contact_id: int, user_id: int | None, first_name: str,
                         last_name: str, phone: str, email: str = "") -> bool:
    """Update a contact. Returns False if the phone clashes with another contact."""
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        conj = "AND" if where else "WHERE"
        cursor = await db.execute(
            f"SELECT id FROM contacts {where} {conj} phone = ? AND id != ?",
            (*params, phone, contact_id),
        )
        if await cursor.fetchone():
            return False
        cursor = await db.execute(
            f"""UPDATE contacts SET first_name = ?, last_name = ?, phone = ?, email = ?
                {where} {conj} id = ?""",
            (first_name or "Contact", last_name or "", phone, email or "",
             *params, contact_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_contact(contact_id: int, user_id: int | None) -> None:
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        cursor = await db.execute(f"SELECT id FROM contacts {where}", params)
        ids = {row[0] for row in await cursor.fetchall()}
        if contact_id not in ids:
            return
        await db.execute("DELETE FROM campaign_contacts WHERE contact_id = ?", (contact_id,))
        await db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        await db.commit()
    finally:
        await db.close()


async def update_campaign_message(campaign_id: int, message: str) -> None:
    """Save the actual message sent for audit trail."""
    db = await _connect()
    try:
        await db.execute(
            "UPDATE campaigns SET message = ? WHERE id = ?",
            (message, campaign_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_campaigns(user_id: int | None = None) -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id, "c")
        cursor = await db.execute(
            f"""SELECT c.*, COUNT(cc.contact_id) as contact_count
               FROM campaigns c
               LEFT JOIN campaign_contacts cc ON c.id = cc.campaign_id
               {where}
               GROUP BY c.id
               ORDER BY c.created_at DESC""",
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def add_contacts_to_campaign(campaign_id: int, contact_ids: list[int]) -> int:
    """Link contacts to a campaign. Returns number actually added."""
    added = 0
    db = await _connect()
    try:
        for cid in contact_ids:
            if cid <= 0:
                continue
            try:
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO campaign_contacts (campaign_id, contact_id) VALUES (?, ?)",
                    (campaign_id, cid),
                )
                if cursor.rowcount > 0:
                    added += 1
            except Exception as e:
                logger.warning(f"Failed to link contact {cid} to campaign {campaign_id}: {e}")
        await db.commit()
    finally:
        await db.close()
    return added


async def get_contacts_for_campaign(campaign_id: int) -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.* FROM contacts c
               INNER JOIN campaign_contacts cc ON c.id = cc.contact_id
               WHERE cc.campaign_id = ?
               ORDER BY c.first_name""",
            (campaign_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_contact_count_for_campaign(campaign_id: int) -> int:
    db = await _connect()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = ?",
            (campaign_id,),
        )
        row = await cursor.fetchone()
        return row[0]
    finally:
        await db.close()


# -- Message Log ----------------------------------------------

async def log_message(
    phone: str,
    content: str = "",
    status: str = "queued",
    campaign_id: int | None = None,
    contact_id: int | None = None,
    wa_message_id: str = "",
    error_message: str = "",
    user_id: int = 1,
    variant_id: int | None = None,
    device_id: str = "",
) -> int:
    db = await _connect()
    try:
        cursor = await db.execute(
            """INSERT INTO message_log
               (user_id, contact_id, campaign_id, phone, content, status, wa_message_id, error_message, variant_id, device_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, contact_id, campaign_id, phone, content, status, wa_message_id, error_message, variant_id, device_id),
        )
        await db.commit()
        return cursor.lastrowid or 0
    finally:
        await db.close()


async def get_message_logs(user_id: int | None, campaign_id: int | None = None,
                         limit: int = 5000) -> list[dict]:
    """Message log rows for CSV export (newest first)."""
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        q = """SELECT sent_at, phone, status, device_id, error_message, campaign_id
               FROM message_log"""
        if where:
            q += " " + where
            if campaign_id:
                q += " AND campaign_id = ?"
                params = (*params, campaign_id)
        elif campaign_id:
            q += " WHERE campaign_id = ?"
            params = (campaign_id,)
        q += " ORDER BY id DESC LIMIT ?"
        cur = await db.execute(q, (*params, limit))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def update_message_status(wa_message_id: str, status: str) -> None:
    db = await _connect()
    try:
        await db.execute(
            "UPDATE message_log SET status = ? WHERE wa_message_id = ?",
            (status, wa_message_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_daily_stats(user_id: int | None = None, days: int = 14) -> list[dict]:
    """Messages per day for the last N days: [{date, sent, failed}]."""
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        q = """SELECT substr(sent_at, 1, 10) AS d,
                      SUM(CASE WHEN status IN ('sent','delivered','read') THEN 1 ELSE 0 END) AS sent,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
               FROM message_log """
        if where:
            q += where + " AND sent_at >= datetime('now', '-' || ? || ' days')"
        else:
            q += "WHERE sent_at >= datetime('now', '-' || ? || ' days')"
        q += " GROUP BY d ORDER BY d"
        cur = await db.execute(q, (*params, days))
        rows = await cur.fetchall()
        return [{"date": r[0], "sent": r[1] or 0, "failed": r[2] or 0} for r in rows]
    finally:
        await db.close()


async def get_send_stats(user_id: int | None = None) -> dict:
    """Get overall sending statistics (per user, or all for admin)."""
    db = await _connect()
    try:
        stats = {}
        where, params = _user_filter(user_id)
        for s in ("queued", "sent", "delivered", "read", "failed"):
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM message_log {where}"
                + (" AND " if where else "WHERE ") + "status = ?",
                (*params, s),
            )
            row = await cursor.fetchone()
            stats[s] = row[0]
        cursor = await db.execute(f"SELECT COUNT(*) FROM message_log {where}", params)
        row = await cursor.fetchone()
        stats["total"] = row[0]
        return stats
    finally:
        await db.close()


async def get_campaign_stats(campaign_id: int) -> dict:
    db = await _connect()
    try:
        stats = {}
        for s in ("queued", "sent", "delivered", "read", "failed"):
            cursor = await db.execute(
                "SELECT COUNT(*) FROM message_log WHERE campaign_id = ? AND status = ?",
                (campaign_id, s),
            )
            row = await cursor.fetchone()
            stats[s] = row[0]
        return stats
    finally:
        await db.close()


async def get_recent_messages(limit: int = 20, user_id: int | None = None) -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        cursor = await db.execute(
            f"SELECT * FROM message_log {where} ORDER BY sent_at DESC LIMIT ?",
            (*params, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


# -- Templates ------------------------------------------------

async def get_all_templates(user_id: int | None = None) -> list[dict]:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        cursor = await db.execute(
            f"SELECT * FROM templates {where} ORDER BY category, name", params)
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_template(name: str, user_id: int | None = None) -> dict | None:
    db = await _connect()
    try:
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id)
        where = where + (" AND " if where else "WHERE ") + "name = ?"
        cursor = await db.execute(f"SELECT * FROM templates {where}", (*params, name))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def create_template(name: str, category: str, body: str, user_id: int = 1) -> None:
    db = await _connect()
    try:
        await db.execute(
            """INSERT INTO templates (user_id, name, category, body) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, name) DO UPDATE SET category = excluded.category, body = excluded.body""",
            (user_id, name, category, body),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_template(name: str, user_id: int | None = None) -> None:
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        where = where + (" AND " if where else "WHERE ") + "name = ?"
        await db.execute(f"DELETE FROM templates {where}", (*params, name))
        await db.commit()
    finally:
        await db.close()


# -- Message Variants (anti-ban rotation) ---------------------

_VARIANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_variants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    campaign_id INTEGER,
    name        TEXT DEFAULT '',
    body        TEXT NOT NULL,
    weight      INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
"""

_SCHEDULED_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    campaign_id     INTEGER,
    phone           TEXT DEFAULT '',
    content         TEXT DEFAULT '',
    variant_id      INTEGER,
    delay_min       REAL DEFAULT 15,
    delay_max       REAL DEFAULT 30,
    scheduled_at    TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    attempts        INTEGER DEFAULT 0,
    error_message   TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_messages(status, scheduled_at);
"""

_DEVICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gowa_device_id  TEXT NOT NULL UNIQUE,
    device_name     TEXT DEFAULT '',
    is_default      INTEGER DEFAULT 0,
    last_logged_in_at TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id);
"""


async def _ensure_extra_tables(db: aiosqlite.Connection):
    await db.executescript(_VARIANT_SCHEMA)
    await db.executescript(_SCHEDULED_SCHEMA)
    await db.executescript(_DEVICES_SCHEMA)
    # Migrate legacy single-device columns into user_devices (idempotent)
    await db.execute(
        """INSERT OR IGNORE INTO user_devices (user_id, gowa_device_id, device_name, is_default)
           SELECT id, gowa_device_id, device_name, 1 FROM users WHERE gowa_device_id <> ''"""
    )
    # variant_id on message_log for audit (which variant was sent)
    cols = await _table_columns(db, "message_log")
    if "variant_id" not in cols:
        await db.execute("ALTER TABLE message_log ADD COLUMN variant_id INTEGER")
    if "device_id" not in cols:
        await db.execute("ALTER TABLE message_log ADD COLUMN device_id TEXT DEFAULT ''")
    cols = await _table_columns(db, "scheduled_messages")
    for col, ddl in (("delay_min", "REAL DEFAULT 15"), ("delay_max", "REAL DEFAULT 30"),
                     ("device_id", "TEXT DEFAULT ''"),
                     ("recurrence", "TEXT DEFAULT 'once'")):
        if col not in cols:
            await db.execute(f"ALTER TABLE scheduled_messages ADD COLUMN {col} {ddl}")
    # blocklist table (opt-out numbers, per user)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS blocked_numbers (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL,
               phone TEXT NOT NULL,
               reason TEXT DEFAULT '',
               created_at TEXT DEFAULT (datetime('now')),
               UNIQUE(user_id, phone))""")
    # persistent login sessions (?s= token)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS user_sessions (
               token_hash TEXT PRIMARY KEY,
               user_id INTEGER NOT NULL,
               created_at TEXT DEFAULT (datetime('now')),
               expires_at TEXT NOT NULL)""")
    await db.commit()
    cols = await _table_columns(db, "campaigns")
    for col, ddl in (("max_duration_min", "REAL DEFAULT 0"), ("max_messages", "INTEGER DEFAULT 0"),
                     ("rotate_devices", "INTEGER DEFAULT 0")):
        if col not in cols:
            await db.execute(f"ALTER TABLE campaigns ADD COLUMN {col} {ddl}")
    await db.commit()


async def create_variant(user_id: int, body: str, campaign_id: int | None = None,
                         name: str = "", weight: int = 1) -> int:
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        cursor = await db.execute(
            "INSERT INTO message_variants (user_id, campaign_id, name, body, weight) VALUES (?, ?, ?, ?, ?)",
            (user_id, campaign_id, name, body, max(1, weight)),
        )
        await db.commit()
        return cursor.lastrowid or 0
    finally:
        await db.close()


async def get_variants(user_id: int, campaign_id: int | None = None) -> list[dict]:
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        if campaign_id is None:
            cursor = await db.execute(
                "SELECT * FROM message_variants WHERE user_id = ? AND campaign_id IS NULL ORDER BY id",
                (user_id,))
        else:
            cursor = await db.execute(
                "SELECT * FROM message_variants WHERE user_id = ? AND campaign_id = ? ORDER BY id",
                (user_id, campaign_id))
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def delete_variant(variant_id: int, user_id: int) -> None:
    db = await _connect()
    try:
        await db.execute("DELETE FROM message_variants WHERE id = ? AND user_id = ?",
                         (variant_id, user_id))
        await db.commit()
    finally:
        await db.close()


# -- Scheduled Messages ---------------------------------------

async def schedule_message(user_id: int, scheduled_at: str, content: str = "",
                           phone: str = "", campaign_id: int | None = None,
                           delay_min: float = 15, delay_max: float = 30,
                           device_id: str = "", recurrence: str = "once") -> int:
    """Schedule a one-shot message or a full campaign. scheduled_at: ISO datetime.
    recurrence: 'once' | 'daily' | 'weekly'."""
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (user_id, campaign_id, phone, content, scheduled_at, delay_min, delay_max, device_id, recurrence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, campaign_id, phone, content, scheduled_at, delay_min, delay_max,
             device_id or "", recurrence),
        )
        await db.commit()
        return cursor.lastrowid or 0
    finally:
        await db.close()


async def get_due_scheduled(limit: int = 50) -> list[dict]:
    """Pending schedules whose time has come."""
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM scheduled_messages
               WHERE status = 'pending' AND scheduled_at <= datetime('now', 'localtime')
               ORDER BY scheduled_at LIMIT ?""",
            (limit,))
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_scheduled(user_id: int | None = None, include_done: bool = False) -> list[dict]:
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        where, params = _user_filter(user_id, "s")
        if not include_done:
            where = where + (" AND " if where else "WHERE ") + "s.status = 'pending'"
        cursor = await db.execute(
            f"""SELECT s.*, u.username FROM scheduled_messages s
                LEFT JOIN users u ON u.id = s.user_id
                {where} ORDER BY s.scheduled_at""", params)
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def update_scheduled_status(sched_id: int, status: str, error: str = "") -> None:
    db = await _connect()
    try:
        await db.execute(
            "UPDATE scheduled_messages SET status = ?, attempts = attempts + 1, error_message = ? WHERE id = ?",
            (status, error, sched_id))
        await db.commit()
    finally:
        await db.close()


async def reschedule_next(sched_id: int, next_at: str) -> None:
    """Roll a recurring schedule forward: reset to pending at next_at."""
    db = await _connect()
    try:
        await db.execute(
            """UPDATE scheduled_messages
               SET scheduled_at = ?, status = 'pending', attempts = 0, error_message = ''
               WHERE id = ?""",
            (next_at, sched_id))
        await db.commit()
    finally:
        await db.close()


async def get_failed_phones(user_id: int | None, campaign_id: int) -> list[dict]:
    """Distinct phones with failed status for a campaign (for retry)."""
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        q = """SELECT DISTINCT phone FROM message_log
               WHERE campaign_id = ? AND status = 'failed'"""
        if where:
            q += " AND " + where.replace("WHERE ", "", 1)
        cur = await db.execute(q, (campaign_id, *params))
        return [{"phone": r[0]} for r in await cur.fetchall()]
    finally:
        await db.close()


async def add_blocked(user_id: int, phone: str, reason: str = "") -> bool:
    """Add a phone to the user's blocklist. Returns False if already blocked."""
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        try:
            await db.execute(
                "INSERT INTO blocked_numbers (user_id, phone, reason) VALUES (?, ?, ?)",
                (user_id, phone, reason))
            await db.commit()
            return True
        except Exception:
            return False
    finally:
        await db.close()


async def remove_blocked(user_id: int, phone: str) -> None:
    db = await _connect()
    try:
        await db.execute(
            "DELETE FROM blocked_numbers WHERE user_id = ? AND phone = ?",
            (user_id, phone))
        await db.commit()
    finally:
        await db.close()


async def list_blocked(user_id: int) -> list[dict]:
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM blocked_numbers WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get_blocked_phones(user_id: int) -> set[str]:
    rows = await list_blocked(user_id)
    return {r["phone"] for r in rows}


# -- Persistent login sessions ---------------------------------

_SESSION_DAYS = 30


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(user_id: int) -> str:
    """Create a persistent login token. Returns the raw token (shown once)."""
    import secrets
    token = secrets.token_urlsafe(32)
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        await db.execute(
            "DELETE FROM user_sessions WHERE expires_at < datetime('now')")
        await db.execute(
            """INSERT INTO user_sessions (token_hash, user_id, expires_at)
               VALUES (?, ?, datetime('now', ?))""",
            (_hash_token(token), user_id, f"+{_SESSION_DAYS} days"))
        await db.commit()
        return token
    finally:
        await db.close()


async def get_session_user(token: str) -> dict | None:
    """Validate a login token; returns the user row or None."""
    if not token:
        return None
    db = await _connect()
    try:
        await _ensure_extra_tables(db)
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT u.* FROM user_sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > datetime('now')""",
            (_hash_token(token),))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def delete_session(token: str) -> None:
    if not token:
        return
    db = await _connect()
    try:
        await db.execute("DELETE FROM user_sessions WHERE token_hash = ?",
                         (_hash_token(token),))
        await db.commit()
    finally:
        await db.close()


async def cancel_scheduled(sched_id: int, user_id: int | None = None) -> None:
    db = await _connect()
    try:
        where, params = _user_filter(user_id)
        where = where + (" AND " if where else "WHERE ") + "id = ?"
        await db.execute(f"UPDATE scheduled_messages SET status = 'cancelled' {where}",
                         (*params, sched_id))
        await db.commit()
    finally:
        await db.close()
