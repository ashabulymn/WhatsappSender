"""
Wasend — Streamlit Web Dashboard (multi-user).

- Login username/password (tabel users), admin bisa lihat semua data
- Perangkat WhatsApp per user (QR pairing via GoWA)
- Kirim campaign: varian pesan acak + jeda random (anti-ban) + jadwal
- Scheduler background untuk pesan terjadwal
- Bilingual UI (Indonesian / English) via i18n module.
"""

import asyncio
import difflib
import html
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

import config
import database
import whatsapp
import csv_handler
import sender
from i18n import t
from whatsapp import (
    WhatsAppClient,
    gowa_create_device,
    gowa_list_devices,
    gowa_device_login,
    gowa_device_login_code,
    gowa_device_info,
    gowa_device_logout,
    gowa_device_delete,
)

logger = logging.getLogger(__name__)

# -- Async Bridge ----------------------------------------------

_loop = asyncio.new_event_loop()
_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_thread.start()


def run_async(coro):
    """Run an async coroutine from sync Streamlit context."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=60)
    except TimeoutError:
        future.cancel()
        raise RuntimeError("Database operation timed out")


def _stat_grid(cards: list[tuple]) -> None:
    """Responsive DarkPan stat-card grid: columns follow content width."""
    _items = "".join(
        f"<div class='stat-card'><div class='stat-icon'>{icon}</div>"
        f"<div><div class='stat-label'>{label}</div>"
        f"<div class='stat-value'>{value}</div></div></div>"
        for icon, label, value in cards)
    st.markdown(f"<div class='stat-grid'>{_items}</div>", unsafe_allow_html=True)



def init_db():
    if "db_init" not in st.session_state:
        run_async(database.init())
        st.session_state.db_init = True


# -- Background Scheduler --------------------------------------

def _process_scheduled(s: dict):
    """Send one due scheduled item. Runs in scheduler thread via run_async."""
    user = run_async(database.get_user(s["user_id"]))
    if not user:
        run_async(database.update_scheduled_status(s["id"], "failed", "user not found"))
        return
    dev = run_async(database.get_default_device(s["user_id"]))
    device_id = s.get("device_id") or (dev["gowa_device_id"] if dev else "")
    if not device_id:
        run_async(database.update_scheduled_status(s["id"], "failed", "no device linked"))
        return
    # Don't double-run a campaign that is already blasting
    ctrl = sender.get_blast_control(s["campaign_id"]) if s.get("campaign_id") else None
    if ctrl and ctrl["status"] in ("running", "paused"):
        logger.info("Scheduler: campaign %s already running, skipping", s["campaign_id"])
        return
    try:
        if s.get("campaign_id"):
            campaign = run_async(database.get_campaign(s["campaign_id"])) or {}
            result = run_async(sender.send_campaign(
                user_id=s["user_id"],
                campaign_id=s["campaign_id"],
                delay_min=s.get("delay_min") or config.GOWA_DELAY_MIN_SECONDS,
                delay_max=s.get("delay_max") or config.GOWA_DELAY_MAX_SECONDS,
                device_id=device_id,
                max_duration_min=campaign.get("max_duration_min") or 0,
                max_messages=campaign.get("max_messages") or 0,
                rotate_devices=bool(campaign.get("rotate_devices")),
            ))
            err = "" if result["failed"] == 0 else f"{result['failed']} failed"
            run_async(database.update_scheduled_status(
                s["id"], "sent" if result["failed"] == 0 else "failed", err))
            # recurring: roll forward instead of finishing
            if (s.get("recurrence") or "once") != "once":
                _days = 7 if s["recurrence"] == "weekly" else 1
                try:
                    _orig = datetime.strptime(s["scheduled_at"][:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    _orig = datetime.now()
                _next = (_orig + timedelta(days=_days)).strftime("%Y-%m-%d %H:%M:%S")
                run_async(database.reschedule_next(s["id"], _next))
        else:
            result = run_async(sender.send_single(
                user_id=s["user_id"], phone=s["phone"], content=s["content"],
                device_id=device_id))
            run_async(database.update_scheduled_status(
                s["id"], result["status"], result.get("error", "")))
    except Exception as e:
        logger.error(f"Scheduler failed for #{s['id']}: {e}")
        run_async(database.update_scheduled_status(s["id"], "failed", str(e)[:200]))


def _device_janitor():
    """Auto-delete dead/abandoned GoWA slots.

    - Slot never paired and older than DEVICE_UNPAIRED_TTL_HOURS (default 24)
      -> deleted (abandoned pairing attempt).
    - Slot once paired but not logged_in for longer than
      DEVICE_DISCONNECT_GRACE_HOURS (default 24) -> deleted.
    Runs inside the scheduler thread; server-side, no browser needed.
    """
    unpaired_ttl_h = float(os.getenv("DEVICE_UNPAIRED_TTL_HOURS", "24") or 24)
    disc_grace_h = float(os.getenv("DEVICE_DISCONNECT_GRACE_HOURS", "24") or 24)
    rows = run_async(database.get_all_device_rows())
    if not rows:
        return
    try:
        raw = whatsapp._gowa_call("GET", "/devices")
    except Exception as e:
        logger.warning("Device janitor: cannot reach GoWA: %s", e)
        return
    if raw.get("code") != "SUCCESS":
        # GoWA unreachable/auth failed: NEVER treat as "all slots gone"
        logger.warning("Device janitor: GoWA list failed (%s), skipping",
                       raw.get("code"))
        return
    live = {d.get("id"): d for d in raw.get("results") or []}
    now = datetime.utcnow()
    for r in rows:
        gid = r["gowa_device_id"]
        state = (live.get(gid) or {}).get("state")
        if state == "logged_in":
            run_async(database.touch_device_login(gid))
            continue
        if state is None:
            # Slot vanished from GoWA: drop the orphan row
            run_async(database.remove_user_device(r["user_id"], gid))
            logger.info("Device janitor: dropped orphan row for missing slot %s", gid)
            continue
        if r.get("last_logged_in_at"):
            try:
                since = datetime.strptime(
                    r["last_logged_in_at"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            dead_h = (now - since).total_seconds() / 3600
            reason = f"disconnected {dead_h:.1f}h"
            overdue = dead_h > disc_grace_h
        else:
            try:
                created = datetime.strptime(
                    r["created_at"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            age_h = (now - created).total_seconds() / 3600
            reason = f"never paired, age {age_h:.1f}h"
            overdue = age_h > unpaired_ttl_h
        if overdue:
            try:
                gowa_device_delete(gid)
            except Exception as e:
                logger.warning("Device janitor: delete %s failed: %s", gid, e)
            run_async(database.remove_user_device(r["user_id"], gid))
            logger.info("Device janitor: auto-deleted %s (user %s, %s)",
                        gid, r.get("username"), reason)


_janitor_last_run = 0.0

def _scheduler_loop():
    global _janitor_last_run
    while True:
        try:
            due = run_async(database.get_due_scheduled(50))
            for s in due:
                _process_scheduled(s)
        except Exception as e:
            logger.error(f"Scheduler tick failed: {e}")
        try:
            if time.time() - _janitor_last_run > 600:  # every 10 min
                _janitor_last_run = time.time()
                _device_janitor()
        except Exception as e:
            logger.error(f"Device janitor tick failed: {e}")
        time.sleep(30)


def start_scheduler():
    global _scheduler_started
    try:
        _scheduler_started
    except NameError:
        _scheduler_started = False
    if not _scheduler_started:
        _scheduler_started = True
        th = threading.Thread(target=_scheduler_loop, daemon=True)
        th.start()
        logger.info("Wasend scheduler started (30s tick)")


# -- Page Config -----------------------------------------------

st.set_page_config(
    page_title="Wasend",
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Hide Streamlit's default menu (Rerun / Settings / Print) and footer
st.markdown(
    """<style>
/* ================= Wasend design system ================= */
:root{
  --wa-bg:#f4f7f5; --wa-card:#ffffff; --wa-line:#e3eae5;
  --wa-ink:#182420; --wa-muted:#64756c;
  --wa-accent:#eb1616; --wa-accent-dark:#c41212; --wa-accent-soft:#fdecec;
}
html.wasend-dark{
  --wa-bg:#0d0f12; --wa-card:#191c24; --wa-line:#2b303c;
  --wa-ink:#e9efeb; --wa-muted:#93a298;
  --wa-accent:#eb1616; --wa-accent-dark:#ff5a5a; --wa-accent-soft:#2b1215;
  --background-color:#0d0f12; --secondary-background-color:#191c24;
  --text-color:#e9efeb; color-scheme:dark;
}
#MainMenu{visibility:hidden;}
footer{visibility:hidden;}

/* surfaces */
.stApp{background-color:var(--wa-bg);}
section[data-testid="stSidebar"]{background-color:var(--wa-card);border-right:1px solid var(--wa-line);}
[data-testid="stHeader"]{background:transparent;}

/* DarkPan sidebar menu */
.side-profile{display:flex;align-items:center;gap:12px;background:var(--wa-bg);
  border:1px solid var(--wa-line);border-radius:12px;padding:12px;margin:4px 0 4px;}
section[data-testid="stSidebar"] .stButton>button,
section[data-testid="stSidebar"] .stButton>button *{
  justify-content:flex-start !important;text-align:left !important;}
section[data-testid="stSidebar"] .stButton>button{
  border:1px solid transparent;background:transparent;color:var(--wa-muted);
  font-weight:600;
  padding:9px 0 !important;border-radius:10px;}
section[data-testid="stSidebar"] .stButton>button div[data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"] .stButton>button div[data-testid="stMarkdownContainer"] p{
  text-align:left !important;width:100%;margin:0 !important;}
section[data-testid="stSidebar"] .stButton>button:hover{
  background:var(--wa-accent-soft);border-color:transparent;color:var(--wa-ink);}
section[data-testid="stSidebar"] .stButton>button[kind="primary"]{
  background:var(--wa-accent-soft);border-color:var(--wa-accent);
  color:var(--wa-accent);font-weight:700;}
section[data-testid="stSidebar"] .stButton>button[kind="primary"]:hover{color:var(--wa-accent);}
[data-testid="stAppViewContainer"]{background-color:var(--wa-bg);}
html.wasend-dark body{background-color:#0d0f12;}

/* buttons */
.stButton>button{border-radius:10px;font-weight:600;background-color:var(--wa-card);
  color:var(--wa-ink);border:1px solid var(--wa-line);transition:all .15s ease;}
.stButton>button:hover{border-color:var(--wa-accent);color:var(--wa-accent-dark);}
.stButton>button:active{transform:scale(.98);}
.stButton>button[kind="primary"]{background-color:var(--wa-accent);border-color:var(--wa-accent);color:#fff;}
.stButton>button[kind="primary"]:hover{background-color:var(--wa-accent-dark);border-color:var(--wa-accent-dark);color:#fff;}
.stButton>button:disabled{opacity:.55;}

/* inputs */
.stTextInput>div>div>input,.stTextArea>div>div>textarea,.stNumberInput>div>div>input,
.stDateInput>div>div>input,.stTimeInput>div>div>input{
  border-radius:10px;background-color:var(--wa-card);color:var(--wa-ink);border:1px solid var(--wa-line);}
.stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus,.stNumberInput>div>div>input:focus,
.stDateInput>div>div>input:focus,.stTimeInput>div>div>input:focus{
  border-color:var(--wa-accent);box-shadow:0 0 0 1px var(--wa-accent);}
div[data-baseweb="select"]>div{background-color:var(--wa-card);border-color:var(--wa-line);border-radius:10px;}

/* metric cards */
[data-testid="stMetric"]{background-color:var(--wa-card);border:1px solid var(--wa-line);
  border-radius:14px;padding:12px 16px;box-shadow:0 1px 3px rgba(20,40,25,.06);}
[data-testid="stMetricLabel"]{color:var(--wa-muted);font-weight:600;}

/* expanders */
[data-testid="stExpander"]{background-color:var(--wa-card);border:1px solid var(--wa-line);border-radius:12px;}

/* alerts */
.stAlert{border-radius:12px;}

/* dataframes */
[data-testid="stDataFrame"]{border:1px solid var(--wa-line);border-radius:12px;overflow:hidden;}

/* progress */
.stProgress>div>div>div{background-color:var(--wa-accent);}

/* DarkPan-style stat cards */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;}
.stat-card{display:flex;align-items:center;gap:14px;background:var(--wa-card);
  border:1px solid var(--wa-line);border-radius:12px;padding:18px 16px;
  box-shadow:0 1px 3px rgba(0,0,0,.10);}
.stat-icon{font-size:36px;line-height:1;flex-shrink:0;}
.stat-label{font-size:13px;color:var(--wa-muted);margin-bottom:2px;}
.stat-value{font-size:26px;font-weight:800;color:var(--wa-ink);line-height:1.1;}

/* message preview bubbles */
.preview-bubble{background:var(--wa-accent-soft);border:1px solid var(--wa-line);
  border-radius:12px;padding:10px 14px;margin:8px 0;white-space:pre-wrap;
  color:var(--wa-ink);}
.preview-bubble .preview-to{font-size:12px;color:var(--wa-muted);margin-bottom:4px;}

/* responsive: stack columns vertically on small screens */
@media (max-width: 640px){
  div[data-testid="stHorizontalBlock"]{flex-wrap:wrap !important;}
  div[data-testid="stHorizontalBlock"]>div{
    flex:1 1 100% !important;min-width:100% !important;max-width:100% !important;}
  .stat-grid{grid-template-columns:repeat(auto-fit,minmax(160px,1fr)) !important;}
}

/* misc */
hr{border-color:var(--wa-line);}
a{color:var(--wa-accent-dark);}
html.wasend-dark a{color:#8ab4ff;}
code{border-radius:6px;}

/* dark-mode text contrast: Streamlit bakes light-theme text colors, override explicitly */
html.wasend-dark .stMarkdown p,html.wasend-dark .stMarkdown li,
html.wasend-dark h1,html.wasend-dark h2,html.wasend-dark h3,
html.wasend-dark .stCaption,html.wasend-dark label,
html.wasend-dark [data-testid="stMetricValue"],
html.wasend-dark [data-testid="stExpander"] summary p,
html.wasend-dark div[data-baseweb="select"] span,
html.wasend-dark table,html.wasend-dark .stDataFrame{color:var(--wa-ink);}
html.wasend-dark .stRadio label p,html.wasend-dark .stCheckbox label p{color:var(--wa-ink);}
html.wasend-dark section[data-testid="stSidebar"] .stRadio label p{color:var(--wa-muted);}
html.wasend-dark section[data-testid="stSidebar"] .stRadio label:has(input:checked) p{color:var(--wa-accent);}
</style>""",
    unsafe_allow_html=True,
)
# Theme toggle switch (pure client-side; default follows OS, saved in localStorage)
_THEME_SWITCH_HTML = """
<div style="display:flex;align-items:center;justify-content:center;gap:5px;height:38px;">
  <span style="font-size:17px;line-height:1;">&#9728;&#65039;</span>
  <label class="wsw"><input type="checkbox" id="wtsw"><span class="wsl"></span></label>
  <span style="font-size:17px;line-height:1;">&#127769;</span>
</div>
<style>
.wsw{position:relative;display:inline-block;width:42px;height:23px;margin:0;}
.wsw input{opacity:0;width:0;height:0;margin:0;}
.wsl{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#c9cdd4;border-radius:23px;transition:.2s;}
.wsl:before{position:absolute;content:"";height:17px;width:17px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.3);}
.wsw input:checked + .wsl{background:#555;}
.wsw input:checked + .wsl:before{transform:translateX(19px);}
</style>
<script>
(function(){
  var KEY='wasend-theme';
  var doc=window.parent.document;
  var box=document.getElementById('wtsw');
  function apply(mode){
    if(mode==='dark'){doc.documentElement.classList.add('wasend-dark');}
    else{doc.documentElement.classList.remove('wasend-dark');}
    box.checked=(mode==='dark');
  }
  var init=null;
  try{init=localStorage.getItem(KEY);}catch(e){}
  if(!init){init=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
  apply(init);
  box.addEventListener('change',function(){
    var m=box.checked?'dark':'light';
    try{localStorage.setItem(KEY,m);}catch(e){}
    apply(m);
  });
  try{window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',function(e){
    var s=null;try{s=localStorage.getItem(KEY);}catch(x){}
    if(!s)apply(e.matches?'dark':'light');
  });}catch(e){}
})();
</script>
"""

# -- Language --------------------------------------------------
def _system_lang() -> str:
    """Default UI language from the browser locale (fallback: config)."""
    try:
        loc = (st.context.locale or "").lower()
    except Exception:
        return config.APP_LANG
    if loc.startswith("id"):
        return "id"
    if loc.startswith("en"):
        return "en"
    return config.APP_LANG

if "lang" not in st.session_state:
    st.session_state.lang = _system_lang()
lang = st.session_state.lang

# -- Authentication Gate ---------------------------------------

init_db()
start_scheduler()


def _refresh_user():
    st.session_state.user = run_async(database.get_user(st.session_state.user["id"]))


if "user" not in st.session_state:
    st.session_state.user = None

# persistent login: restore from ?s= token after reload
if not st.session_state.user:
    _tok = st.query_params.get("s", "")
    if _tok:
        _u = run_async(database.get_session_user(_tok))
        if _u:
            st.session_state.user = _u

if not st.session_state.user:
    _lg1, _lg2, _lg3 = st.columns([1, 1.5, 1])
    with _lg2:
        st.markdown("<div style='text-align:center;font-size:56px;line-height:1.3;'>📱</div>",
                    unsafe_allow_html=True)
        st.markdown(f"<h1 style='text-align:center;margin-bottom:0.5rem;'>{t('auth.login_title', lang)}</h1>",
                    unsafe_allow_html=True)
        username = st.text_input(t("auth.username", lang))
        password = st.text_input(t("auth.password", lang), type="password")
        if st.button(t("auth.login_btn", lang), type="primary", use_container_width=True):
            u = run_async(database.get_user_by_username(username))
            if u and database.verify_password(password, u["password_hash"]):
                st.session_state.user = u
                st.query_params["s"] = run_async(database.create_session(u["id"]))
                st.rerun()
            else:
                st.error(t("auth.wrong", lang))
    st.stop()

user = st.session_state.user
uid = user["id"]
_impersonating = bool(st.session_state.get("impersonating"))
is_admin = bool(user.get("is_admin")) and not _impersonating
_default_dev = run_async(database.get_default_device(uid))
device_id = _default_dev["gowa_device_id"] if _default_dev else ""

if _impersonating:
    st.warning(t("admin.impersonating", lang).format(u=user["username"]))
    if st.button(t("admin.back_to_admin", lang), key="back_to_admin"):
        st.session_state.user = st.session_state.get("real_admin")
        st.session_state.impersonating = False
        st.session_state.real_admin = None
        st.rerun()

# -- Sidebar ---------------------------------------------------

# -- Sidebar (DarkPan style) -----------------------------------

with st.sidebar:
    st.markdown(
        "<div style='font-size:24px;font-weight:800;color:var(--wa-accent);"
        "line-height:1.15;'>📱 Wasend</div>"
        f"<div style='font-size:11px;color:var(--wa-muted);margin-bottom:8px;'>"
        f"v{config.BOT_VERSION} | {config.WA_PROVIDER.upper()}</div>",
        unsafe_allow_html=True)
    _role = t("role.admin", lang) if is_admin else t("role.user", lang)
    st.markdown(
        f"<div class='side-profile'><div style='font-size:30px;line-height:1;'>👤</div>"
        f"<div><div style='font-weight:700;color:var(--wa-ink);'>{user['username']}</div>"
        f"<div style='font-size:12px;color:var(--wa-muted);'>{_role}</div></div></div>",
        unsafe_allow_html=True)
    if is_admin:
        view_all = st.checkbox(t("admin.view_all", lang), key="view_all")
    else:
        view_all = False

    st.divider()
    if "page" not in st.session_state:
        st.session_state.page = "dashboard"
    _NAV_ITEMS = [
        ("dashboard", t("dash.page_dashboard", lang)),
        ("stats", t("dash.page_stats", lang)),
        ("import", t("dash.page_import", lang)),
        ("send", t("dash.page_send", lang)),
        ("schedule", "🗓️ " + t("dash.page_schedule", lang)),
        ("contacts", t("dash.page_contacts", lang)),
        ("templates", t("dash.page_templates", lang)),
        ("device", "📲 " + t("dash.page_device", lang)),
        ("test", t("dash.page_test", lang)),
        ("config", t("dash.page_config", lang)),
    ] + ([("admin", "👑 " + t("dash.page_admin", lang))] if is_admin else [])
    if st.session_state.page not in [k for k, _ in _NAV_ITEMS]:
        st.session_state.page = "dashboard"
    for _key, _label in _NAV_ITEMS:
        if st.button(_label, key=f"nav_{_key}", use_container_width=True,
                     type="primary" if st.session_state.page == _key else "secondary"):
            st.session_state.page = _key
            st.rerun()
    page = st.session_state.page

    st.divider()
    _b1, _b2 = st.columns([1, 1.7])
    with _b1:
        if st.button("🇮🇩" if lang == "id" else "🇬🇧", key="lang_btn"):
            st.session_state.lang = "en" if lang == "id" else "id"
            st.rerun()
    with _b2:
        components.html(_THEME_SWITCH_HTML, height=38)
    if st.button("🚪 " + t("auth.logout", lang), use_container_width=True,
                 key="logout_btn"):
        run_async(database.delete_session(st.query_params.get("s", "")))
        st.query_params.clear()
        st.session_state.user = None
        st.session_state.impersonating = False
        st.session_state.real_admin = None
        st.rerun()
    st.markdown(f"[GitHub]({config.COMMUNITY_URL}) | [Site]({config.WEBSITE_URL})")


# view_uid: None = admin melihat semua
view_uid = None if (is_admin and view_all) else uid


def _need_device() -> bool:
    """Warn if user has no linked device. Returns True when blocked."""
    if not device_id:
        st.warning(t("device.need_device", lang))
        return True
    return False


def _run_blast_thread(uid, campaign_id, variants, delay_min, delay_max,
                      device_id, max_dur, max_msg, only_phones=None,
                      rotate_devices=False):
    """Background blast runner (daemon thread). Progress via blast control."""
    try:
        run_async(sender.send_campaign(
            user_id=uid, campaign_id=campaign_id, variants=variants,
            delay_min=delay_min, delay_max=delay_max, device_id=device_id,
            max_duration_min=max_dur, max_messages=max_msg,
            only_phones=only_phones, rotate_devices=rotate_devices,
        ))
    except Exception as e:
        logger.error("Blast thread failed: %s", e)
        ctrl = sender.get_blast_control(campaign_id)
        ctrl["status"] = "error"
        ctrl["stop_reason"] = str(e)[:200]


# -- Dashboard Page --------------------------------------------

if page == "dashboard":
    st.title(t("dash.page_dashboard", lang))

    contact_count = run_async(database.get_contact_count(view_uid))
    campaigns = run_async(database.get_all_campaigns(view_uid))
    stats = run_async(database.get_send_stats(view_uid))

    total_out = sum(stats.get(s, 0) for s in ("sent", "delivered", "read", "failed"))
    delivered = stats.get("delivered", 0) + stats.get("read", 0)
    rate = (delivered / total_out * 100) if total_out > 0 else 0

    _stat_grid([
        ("👥", t("dash.metric_contacts", lang), contact_count),
        ("📢", t("dash.metric_campaigns", lang), len(campaigns)),
        ("📤", t("dash.metric_sent", lang),
         stats.get("sent", 0) + stats.get("delivered", 0)),
        ("❌", t("dash.metric_failed", lang), stats.get("failed", 0)),
        ("📈", t("dash.metric_delivery_rate", lang), f"{rate:.1f}%"),
        ("✅", t("dash.metric_delivered", lang), stats.get("delivered", 0)),
        ("👁️", t("dash.metric_read", lang), stats.get("read", 0)),
        ("📊", t("dash.metric_total", lang), stats.get("total", 0)),
    ])

    st.divider()
    _daily = run_async(database.get_daily_stats(view_uid, 14))
    _has_data = bool(_daily) and any(d["sent"] or d["failed"] for d in _daily)
    if _has_data:
        _df = pd.DataFrame(_daily)
        _df["date"] = pd.to_datetime(_df["date"]).dt.strftime("%d/%m")
        _df = _df.set_index("date")
    _c1, _c2 = st.columns(2)
    with _c1:
        st.subheader(t("dash.chart_daily", lang))
        if _has_data:
            st.bar_chart(_df[["sent"]], color="#eb1616")
        else:
            st.caption(t("dash.no_chart_data", lang))
    with _c2:
        st.subheader(t("dash.chart_status", lang))
        if _has_data:
            st.area_chart(_df[["sent", "failed"]], color=["#eb1616", "#8a8f9e"])
        else:
            st.caption(t("dash.no_chart_data", lang))

    st.divider()
    st.subheader(t("dash.recent_campaigns", lang))
    if campaigns:
        for c in campaigns[:5]:
            status_emoji = {"draft": "📝", "sent": "✅", "sending": "📤"}.get(c["status"], "⏳")
            st.markdown(f"{status_emoji} **{c['name']}** — {c.get('contact_count', 0)} {t('dash.contacts_suffix', lang)} — {c['created_at'][:16]}")
    else:
        st.caption(t("dash.no_campaigns", lang))


# -- Import CSV Page -------------------------------------------

elif page == "import":
    st.title(t("dash.import_title", lang))

    uploaded = st.file_uploader(t("dash.choose_csv", lang), type=["csv"])
    campaign_name = st.text_input(
        t("dash.campaign_name", lang),
        placeholder=t("dash.campaign_name_ph", lang),
    )

    if uploaded and campaign_name:
        if st.button(t("dash.import_btn", lang), type="primary"):
            content = uploaded.read()
            contacts, errors = csv_handler.parse_csv(content, lang=lang)

            if errors:
                with st.expander(t("dash.warnings_n", lang).format(n=len(errors))):
                    for err in errors[:20]:
                        st.text(err)

            if contacts:
                with st.spinner(t("dash.importing_n", lang).format(n=len(contacts))):
                    contact_ids = []
                    for c in contacts:
                        cid = run_async(database.upsert_contact(
                            first_name=c["first_name"],
                            last_name=c["last_name"],
                            phone=c["phone"],
                            email=c.get("email", ""),
                            user_id=uid,
                        ))
                        contact_ids.append(cid)

                    campaign_id = run_async(database.create_campaign(
                        name=campaign_name, message="", user_id=uid))
                    run_async(database.add_contacts_to_campaign(campaign_id, contact_ids))

                st.success(t("dash.imported_n", lang).format(n=len(contacts), name=campaign_name))
                df = pd.DataFrame(contacts)
                st.dataframe(df.head(20), use_container_width=True)
            else:
                st.error(t("dash.no_valid_contacts", lang))

    st.divider()
    st.subheader(t("dash.csv_format_title", lang))
    st.code("phone,first_name,last_name,email\n+6281234567890,Budi,Santoso,budi@email.com\n+6289876543210,Siti,Aminah,", language="csv")


# -- Send Page (variants + random delay + schedule) ------------

elif page == "send":
    st.title(t("dash.send_title", lang))
    if _need_device():
        st.stop()

    campaigns = run_async(database.get_all_campaigns(view_uid))
    if not campaigns:
        st.warning(t("dash.no_campaign_import_first", lang))
        st.stop()

    campaign_options = {
        f"{c['name']} ({c.get('contact_count', 0)} {t('dash.contacts_suffix', lang)}) [{c['status']}]": c["id"]
        for c in campaigns
    }
    selected = st.selectbox(t("dash.campaign_label", lang), list(campaign_options.keys()))
    campaign_id = campaign_options[selected]

    st.subheader(t("send.step1", lang))
    contacts = run_async(database.get_contacts_for_campaign(campaign_id))
    st.info(t("dash.contacts_in_this_campaign", lang).format(n=len(contacts)))

    # --- Device picker (when user has more than one device) ---
    _devs = run_async(database.get_user_devices(uid))
    send_device_id = device_id
    rotate_devices = False
    if len(_devs) > 1:
        _dopts = {}
        for _d in _devs:
            _lbl = (_d["device_name"] or _d["gowa_device_id"])
            if _d["is_default"]:
                _lbl = "\u2b50 " + _lbl
            _dopts[_lbl] = _d["gowa_device_id"]
        _def_lbl = next((k for k, v in _dopts.items() if v == device_id),
                        list(_dopts.keys())[0])
        _sel_lbl = st.selectbox(t("send.device_label", lang),
                                list(_dopts.keys()),
                                index=list(_dopts.keys()).index(_def_lbl))
        send_device_id = _dopts[_sel_lbl]
        _camp = run_async(database.get_campaign(campaign_id)) or {}
        rotate_devices = st.checkbox(
            t("send.rotate_devices", lang),
            value=bool(_camp.get("rotate_devices")),
            help=t("send.rotate_help", lang))

    # --- Message variants ---
    st.subheader(t("send.step2", lang))
    if "variant_count" not in st.session_state:
        st.session_state.variant_count = 1
    # preload saved variants for this campaign
    if st.session_state.get("variant_campaign") != campaign_id:
        saved = run_async(database.get_variants(uid, campaign_id))
        st.session_state.variant_count = max(1, len(saved))
        for i, v in enumerate(saved):
            st.session_state[f"variant_{i}"] = v["body"]
        st.session_state.variant_campaign = campaign_id

    variants_input = []
    for i in range(st.session_state.variant_count):
        txt = st.text_area(
            f"Varian {i + 1}",
            key=f"variant_{i}",
            height=120,
            placeholder=t("send.variant_ph", lang).format(n=i + 1),
            help=t("dash.placeholders_help", lang),
        )
        if txt.strip():
            variants_input.append(txt.strip())
    if st.button(t("send.add_variant", lang)):
        st.session_state.variant_count += 1
        st.rerun()

    # --- Variant quality warnings (anti-ban) ---
    if variants_input:
        if len(variants_input) < 3:
            st.warning(t("send.warn_few_variants", lang))
        _too_similar = False
        for _i in range(len(variants_input)):
            for _j in range(_i + 1, len(variants_input)):
                if difflib.SequenceMatcher(
                        None, variants_input[_i], variants_input[_j]).ratio() > 0.8:
                    _too_similar = True
                    break
            if _too_similar:
                break
        if _too_similar:
            st.warning(t("send.warn_similar_variants", lang))

    # --- Preview: rendered samples in chat bubbles ---
    if st.button(t("send.preview_btn", lang),
                 disabled=not (variants_input and contacts)):
        _rng = random.Random()
        _pool = [{"body": b, "weight": 1} for b in variants_input]
        for _k in range(min(3, len(variants_input))):
            _v = sender.pick_variant(_pool, _rng)
            _c = contacts[_k % len(contacts)]
            _msg = html.escape(sender.render_message(_v["body"], _c, _rng))
            _nm = html.escape(_c.get("first_name") or _c.get("phone", ""))
            st.markdown(
                f"<div class='preview-bubble'><div class='preview-to'>➤ {_nm}</div>"
                f"{_msg}</div>",
                unsafe_allow_html=True)

    # --- Random delay range + blast limits ---
    st.subheader(t("send.step3", lang))
    dcol1, dcol2 = st.columns(2)
    delay_min = dcol1.number_input(t("send.delay_min", lang), min_value=1.0, value=15.0, step=1.0)
    delay_max = dcol2.number_input(t("send.delay_max", lang), min_value=1.0, value=30.0, step=1.0)
    if delay_max < delay_min:
        delay_max = delay_min

    # --- Blast limits (optional) ---
    st.subheader(t("send.limits_label", lang))
    lcol1, lcol2 = st.columns(2)
    _camp_row = next((c for c in campaigns if c["id"] == campaign_id), {})
    limit_minutes = lcol1.number_input(
        t("send.limit_minutes", lang), min_value=0.0, step=15.0,
        value=float(_camp_row.get("max_duration_min") or 0))
    limit_messages = lcol2.number_input(
        t("send.limit_messages", lang), min_value=0, step=10,
        value=int(_camp_row.get("max_messages") or 0))

    # --- Send now or schedule ---
    st.subheader(t("send.step4", lang))
    schedule_it = st.checkbox(t("send.schedule_toggle", lang))
    sched_dt = None
    if schedule_it:
        scol1, scol2 = st.columns(2)
        sdate = scol1.date_input(t("sched.datetime", lang), value=datetime.now().date())
        stime = scol2.time_input("", value=(datetime.now() + timedelta(hours=1)).time())
        sched_dt = datetime.combine(sdate, stime)
        _rec_opts = [t("sched.rec_once", lang), t("sched.rec_daily", lang),
                     t("sched.rec_weekly", lang)]
        _rec_sel = st.selectbox(t("sched.recurrence", lang), _rec_opts)
        _recurrence = ("daily" if _rec_sel == _rec_opts[1]
                       else "weekly" if _rec_sel == _rec_opts[2] else "once")

    _ctrl = sender.get_blast_control(campaign_id)
    _blast_active = _ctrl["status"] in ("running", "paused")

    btn_label = t("sched.schedule_btn", lang) if schedule_it else t("dash.send_btn", lang)
    if st.button(btn_label, type="primary",
                 disabled=(not variants_input or not contacts or _blast_active)):
        # persist limits on the campaign first
        run_async(database.update_campaign_limits(campaign_id, limit_minutes, int(limit_messages)))
        run_async(database.set_campaign_rotation(campaign_id, rotate_devices))
        if schedule_it:
            if sched_dt <= datetime.now():
                st.error(t("sched.past", lang))
            else:
                # save variants then schedule
                for v in run_async(database.get_variants(uid, campaign_id)):
                    run_async(database.delete_variant(v["id"], uid))
                for b in variants_input:
                    run_async(database.create_variant(uid, b, campaign_id))
                run_async(database.schedule_message(
                    uid, sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    campaign_id=campaign_id,
                    content="", phone="",
                    delay_min=delay_min, delay_max=delay_max,
                    device_id=send_device_id, recurrence=_recurrence))
                st.success(t("sched.scheduled", lang).format(t=sched_dt.strftime("%d %b %Y %H:%M")))
        else:
            # save variants
            for v in run_async(database.get_variants(uid, campaign_id)):
                run_async(database.delete_variant(v["id"], uid))
            db_variants = []
            for b in variants_input:
                vid = run_async(database.create_variant(uid, b, campaign_id))
                db_variants.append({"id": vid, "body": b, "weight": 1})
            sender.reset_blast_control(campaign_id)
            th = threading.Thread(
                target=_run_blast_thread,
                args=(uid, campaign_id, db_variants, delay_min, delay_max,
                      send_device_id, limit_minutes, int(limit_messages),
                      None, rotate_devices),
                daemon=True,
            )
            th.start()
            st.rerun()

    # --- Blast progress + play/pause/stop ---
    _ctrl = sender.get_blast_control(campaign_id)
    _st = _ctrl["status"]
    if _st in ("running", "paused"):
        st.divider()
        st.subheader(t("blast.progress_title", lang))
        _total = max(1, _ctrl["total"])
        _done = _ctrl["sent"] + _ctrl["failed"]
        st.progress(min(1.0, _done / _total))
        st.text(t("blast.progress_text", lang).format(
            s=_ctrl["sent"], f=_ctrl["failed"], total=_ctrl["total"],
            status=t("blast.st_" + _st, lang)))
        pcol1, pcol2, pcol3 = st.columns(3)
        if _st == "running":
            if pcol1.button(t("blast.pause", lang)):
                _ctrl["pause"].set()
                st.rerun()
        else:
            if pcol1.button(t("blast.resume", lang)):
                _ctrl["pause"].clear()
                st.rerun()
        if pcol2.button(t("blast.stop", lang)):
            _ctrl["stop"].set()
            _ctrl["pause"].clear()
            st.rerun()
        if pcol3.button(t("blast.refresh", lang)):
            st.rerun()
        if _st == "running":
            time.sleep(2)
            st.rerun()
    elif _st in ("done", "stopped", "error") and _ctrl["total"]:
        st.divider()
        _reason = _ctrl.get("stop_reason") or ""
        _rsuffix = ""
        if _reason in ("manual", "limit_time", "limit_messages"):
            _rsuffix = " (" + t("blast.reason_" + _reason, lang) + ")"
        st.info(t("blast.finished", lang).format(
            s=_ctrl["sent"], f=_ctrl["failed"],
            status=t("blast.st_" + _st, lang)) + _rsuffix)
        # completion toast (once per finished blast)
        _pkey = f"_blast_prev_{campaign_id}"
        _prev = st.session_state.get(_pkey)
        if _prev in ("running", "paused") and _st != _prev:
            _icon = "✅" if _st == "done" else "⚠️"
            st.toast(f"{_icon} " + t("blast.toast_done", lang).format(
                s=_ctrl["sent"], f=_ctrl["failed"]), icon=_icon)
    st.session_state[f"_blast_prev_{campaign_id}"] = _st

    # --- Retry failed ---
    _failed = run_async(database.get_failed_phones(uid, campaign_id))
    # only retry numbers still in this campaign
    _camp_phones = {c.get("phone") for c in contacts}
    _failed = [f for f in _failed if f["phone"] in _camp_phones]
    if _failed:
        st.divider()
        st.subheader(t("send.retry_title", lang))
        st.caption(t("send.retry_desc", lang).format(n=len(_failed)))
        if st.button(t("send.retry_btn", lang), type="secondary",
                     disabled=_blast_active):
            sender.reset_blast_control(campaign_id)
            _rvariants = [{"id": 0, "body": b, "weight": 1} for b in variants_input] \
                or run_async(database.get_variants(uid, campaign_id))
            th = threading.Thread(
                target=_run_blast_thread,
                args=(uid, campaign_id, _rvariants, delay_min, delay_max,
                      send_device_id, limit_minutes, int(limit_messages),
                      [f["phone"] for f in _failed], rotate_devices),
                daemon=True,
            )
            th.start()
            st.rerun()


# -- Schedule Page ---------------------------------------------

elif page == "schedule":
    st.title(t("sched.title", lang))
    if _need_device():
        st.stop()

    with st.expander(t("sched.new", lang)):
        phone = st.text_input(t("sched.phone", lang), placeholder=t("dash.phone_ph", lang))
        message = st.text_area(t("sched.message", lang), height=120,
                               help=t("dash.placeholders_help", lang))
        c1, c2 = st.columns(2)
        sdate = c1.date_input(t("sched.datetime", lang), value=datetime.now().date(), key="sched_date")
        stime = c2.time_input("", value=(datetime.now() + timedelta(hours=1)).time(), key="sched_time")
        if st.button(t("sched.schedule_btn", lang), type="primary", disabled=not phone or not message):
            sched_dt = datetime.combine(sdate, stime)
            if sched_dt <= datetime.now():
                st.error(t("sched.past", lang))
            else:
                run_async(database.schedule_message(
                    uid, sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    phone=phone, content=message))
                st.success(t("sched.scheduled", lang).format(t=sched_dt.strftime("%d %b %Y %H:%M")))
                st.rerun()

    st.divider()
    st.subheader(t("sched.list_title", lang))
    items = run_async(database.get_scheduled(view_uid))
    if not items:
        st.caption(t("sched.empty", lang))
    else:
        for s in items:
            desc = s["content"][:60] or f"{t('sched.campaign', lang)} #{s['campaign_id']}"
            _rec = (s.get("recurrence") or "once")
            _rec_lbl = {"daily": "🔁 " + t("sched.rec_daily", lang),
                        "weekly": "🔁 " + t("sched.rec_weekly", lang)}.get(_rec, "")
            cols = st.columns([3, 2, 2, 1])
            cols[0].text(f"{s['phone'] or ''} {desc} {_rec_lbl}")
            cols[1].text(s["scheduled_at"])
            cols[2].text(s["status"])
            if cols[3].button(t("sched.cancel", lang), key=f"cancel_{s['id']}"):
                run_async(database.cancel_scheduled(s["id"], view_uid))
                st.success(t("sched.cancelled", lang))
                st.rerun()


# -- Contacts Page ---------------------------------------------

elif page == "contacts":
    st.title(t("dash.contacts_title", lang))

    with st.expander(t("contacts.add_title", lang)):
        ac1, ac2 = st.columns(2)
        afn = ac1.text_input(t("contacts.first_name", lang), key="add_fn")
        aln = ac2.text_input(t("contacts.last_name", lang), key="add_ln")
        ac3, ac4 = st.columns(2)
        aph = ac3.text_input(t("contacts.phone", lang), placeholder=t("dash.phone_ph", lang),
                             key="add_ph")
        aem = ac4.text_input(t("contacts.email", lang), key="add_em")
        if st.button(t("contacts.save", lang), type="primary", key="add_save"):
            if not aph.strip():
                st.error(t("contacts.phone_required", lang))
            else:
                cid = run_async(database.upsert_contact(
                    afn.strip(), aln.strip(), aph.strip(), aem.strip(), view_uid))
                if cid:
                    st.success(t("contacts.saved", lang))
                    st.rerun()
                else:
                    st.error(t("contacts.save_failed", lang))

    contacts = run_async(database.get_all_contacts(view_uid))
    if not contacts:
        st.info(t("dash.no_contacts_import_csv", lang))
    else:
        st.metric(t("dash.total_contacts", lang), len(contacts))
        df = pd.DataFrame(contacts)
        display_cols = [c for c in ["phone", "first_name", "last_name", "email", "created_at"] if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True)

        with st.expander(t("contacts.edit_title", lang)):
            _copts = {f"{c['phone']} — {c.get('first_name') or ''} {c.get('last_name') or ''}".strip(): c
                      for c in contacts}
            _csel = st.selectbox(t("contacts.choose", lang), list(_copts.keys()))
            _c = _copts[_csel]
            ec1, ec2 = st.columns(2)
            efn = ec1.text_input(t("contacts.first_name", lang), value=_c.get("first_name") or "",
                                 key="edit_fn")
            eln = ec2.text_input(t("contacts.last_name", lang), value=_c.get("last_name") or "",
                                 key="edit_ln")
            ec3, ec4 = st.columns(2)
            eph = ec3.text_input(t("contacts.phone", lang), value=_c.get("phone") or "",
                                 key="edit_ph")
            eem = ec4.text_input(t("contacts.email", lang), value=_c.get("email") or "",
                                 key="edit_em")
            bcol1, bcol2 = st.columns(2)
            if bcol1.button(t("contacts.save", lang), type="primary", key="edit_save"):
                ok = run_async(database.update_contact(
                    _c["id"], view_uid, efn.strip(), eln.strip(), eph.strip(), eem.strip()))
                if ok:
                    st.success(t("contacts.saved", lang))
                    st.rerun()
                else:
                    st.error(t("contacts.phone_conflict", lang))
            if bcol2.button(t("contacts.delete", lang), key="edit_del"):
                run_async(database.delete_contact(_c["id"], view_uid))
                st.success(t("contacts.deleted", lang))
                st.rerun()

    # --- Blocklist (opt-out) ---
    st.divider()
    st.subheader(t("block.title", lang))
    st.caption(t("block.desc", lang))
    _b1, _b2 = st.columns([3, 1])
    _bphone = _b1.text_input(t("block.phone", lang), key="block_phone",
                             placeholder=t("dash.phone_ph", lang))
    if _b2.button(t("block.add", lang), key="block_add"):
        _norm = csv_handler.normalize_phone(_bphone.strip()) if _bphone.strip() else None
        if not _norm:
            st.error(t("contacts.phone_required", lang))
        elif run_async(database.add_blocked(uid, _norm)):
            st.success(t("block.added", lang))
            st.rerun()
        else:
            st.info(t("block.already", lang))
    _blocked = run_async(database.list_blocked(uid))
    if _blocked:
        for _b in _blocked:
            _bc1, _bc2 = st.columns([4, 1])
            _bc1.text(f"🚫 {_b['phone']}")
            if _bc2.button(t("block.remove", lang), key=f"unblock_{_b['id']}"):
                run_async(database.remove_blocked(uid, _b["phone"]))
                st.rerun()
    else:
        st.caption(t("block.empty", lang))


# -- Statistics Page -------------------------------------------

elif page == "stats":
    st.title(t("dash.stats_title", lang))
    stats = run_async(database.get_send_stats(view_uid))

    col1, col2 = st.columns(2)
    with col1:
        st.subheader(t("dash.messages_by_status", lang))
        data = {
            t("dash.status_sent", lang): stats.get("sent", 0),
            t("dash.status_delivered", lang): stats.get("delivered", 0),
            t("dash.status_read", lang): stats.get("read", 0),
            t("dash.status_failed", lang): stats.get("failed", 0),
            t("dash.status_pending", lang): stats.get("queued", 0),
        }
        df = pd.DataFrame({
            t("dash.col_status", lang): list(data.keys()),
            t("dash.col_count", lang): list(data.values()),
        })
        st.bar_chart(df.set_index(t("dash.col_status", lang)))
    with col2:
        st.subheader(t("dash.campaigns_label", lang))
        campaigns = run_async(database.get_all_campaigns(view_uid))
        if campaigns:
            for c in campaigns[:5]:
                camp_stats = run_async(database.get_campaign_stats(c["id"]))
                total = sum(camp_stats.values())
                delivered = camp_stats.get("delivered", 0) + camp_stats.get("read", 0)
                rate = (delivered / total * 100) if total > 0 else 0
                st.markdown(f"**{c['name']}** — {c['created_at'][:10]} — {t('dash.rate_n', lang).format(rate=f'{rate:.0f}')}")
        else:
            st.caption(t("dash.no_campaigns", lang))

    st.divider()
    st.subheader(t("dash.recent_messages", lang))
    messages = run_async(database.get_recent_messages(20, view_uid))
    if messages:
        df_msg = pd.DataFrame(messages)
        display_cols = [c for c in ["phone", "status", "content", "sent_at"] if c in df_msg.columns]
        st.dataframe(df_msg[display_cols], use_container_width=True)
    else:
        st.caption(t("dash.no_messages", lang))

    # --- Export log to CSV ---
    st.divider()
    st.subheader(t("stats.export_title", lang))
    _exp_camps = run_async(database.get_all_campaigns(view_uid))
    _exp_opts = {t("stats.export_all", lang): None}
    _exp_opts.update({c["name"]: c["id"] for c in _exp_camps})
    _exp_sel = st.selectbox(t("stats.export_campaign", lang), list(_exp_opts.keys()))
    _rows = run_async(database.get_message_logs(view_uid, _exp_opts[_exp_sel]))
    if _rows:
        import csv as _csv, io as _io
        _buf = _io.StringIO()
        _w = _csv.DictWriter(_buf, fieldnames=["sent_at", "phone", "status", "device_id",
                                              "campaign_id", "error_message"])
        _w.writeheader()
        _w.writerows(_rows)
        st.download_button(
            t("stats.export_btn", lang).format(n=len(_rows)),
            data=_buf.getvalue().encode("utf-8"),
            file_name=f"wasend-log-{datetime.now().strftime('%Y%m%d-%H%M')}.csv",
            mime="text/csv")
    else:
        st.caption(t("stats.export_empty", lang))


# -- Templates Page --------------------------------------------

elif page == "templates":
    st.title(t("dash.templates_title", lang))
    templates = run_async(database.get_all_templates(view_uid))

    with st.expander(t("dash.create_template", lang)):
        name = st.text_input(t("dash.template_name", lang))
        category = st.selectbox(t("dash.category", lang), ["general", "broadcast", "info"])
        body = st.text_area(t("dash.content", lang), height=150,
                            help=t("dash.placeholders_help", lang))
        if st.button(t("dash.create_btn", lang)) and name and body:
            run_async(database.create_template(name, category, body, user_id=uid))
            st.success(t("dash.template_created", lang).format(name=name))
            st.rerun()

    for tpl in templates:
        with st.expander(f"📄 {tpl['name']} [{tpl['category']}]"):
            st.text(tpl["body"])
            if st.button(f"{t('dash.delete_label', lang)} {tpl['name']}", key=f"del_{tpl['id']}"):
                run_async(database.delete_template(tpl["name"], user_id=uid))
                st.success(t("dash.template_deleted", lang).format(name=tpl["name"]))
                st.rerun()


# -- Device Page -----------------------------------------------

elif page == "device":
    st.title(t("device.title", lang))
    max_dev = database.MAX_DEVICES_PER_USER
    devs = run_async(database.get_user_devices(uid))
    st.caption(t("device.my_devices", lang).format(n=len(devs), m=max_dev))

    def _claim_success(gid, label):
        try:
            run_async(database.add_user_device(uid, gid, label))
        except ValueError:
            st.error(t("device.max_reached", lang).format(n=max_dev))
            return
        run_async(database.touch_device_login(gid))
        st.session_state.pop("pair_device_id", None)
        st.session_state.pop("pair_code", None)
        st.success(t("device.linked", lang).format(n=label))
        st.rerun()

    pair_id = st.session_state.get("pair_device_id")
    if pair_id:
        method = st.radio(
            "",
            [t("device.method_qr", lang), t("device.method_code", lang)],
            horizontal=True,
            key="pair_method",
        )
        if method == t("device.method_qr", lang):
            login = gowa_device_login(pair_id)
            if login["ok"]:
                st.markdown(t("device.scan_qr", lang))
                st.image(login["qr_link"], width=280)
                st.caption(t("device.waiting", lang))
                cc1, cc2 = st.columns(2)
                if cc1.button(t("device.qr_reload", lang)):
                    st.rerun()
                if cc2.button(t("device.check_status", lang)):
                    info = gowa_device_info(pair_id)
                    if info["ok"] and info.get("state") == "logged_in":
                        _claim_success(pair_id, info.get("display_name")
                                       or st.session_state.get("pair_device_name", "")
                                       or pair_id)
                    else:
                        st.info(f"{t('device.state', lang)}: {info.get('state', '?')}")
            else:
                st.error(login["error"])
        else:
            st.markdown(t("device.code_help", lang))
            pphone = st.text_input(t("device.phone_label", lang),
                                   placeholder=t("dash.phone_ph", lang),
                                   key="pair_phone")
            if st.button(t("device.get_code", lang), type="primary") and pphone:
                res = gowa_device_login_code(pair_id, pphone)
                if res["ok"]:
                    st.session_state.pair_code = res["pair_code"]
                else:
                    st.error(res["error"])
            if st.session_state.get("pair_code"):
                st.markdown(f"### 🔢 `{st.session_state.pair_code}`")
                if st.button(t("device.check_status", lang), key="code_check"):
                    info = gowa_device_info(pair_id)
                    if info["ok"] and info.get("state") == "logged_in":
                        _claim_success(pair_id, info.get("display_name")
                                       or st.session_state.get("pair_device_name", "")
                                       or pair_id)
                    else:
                        st.info(f"{t('device.state', lang)}: {info.get('state', '?')}")
        st.divider()

    for d in devs:
        gid = d["gowa_device_id"]
        info = gowa_device_info(gid)
        if info["ok"]:
            state = info.get("state", "?")
            phone = info.get("phone") or "-"
        else:
            state, phone = "?", "-"
        label = d["device_name"] or gid
        star = "⭐ " if d["is_default"] else ""
        with st.expander(f"{star}{label} — {state}", expanded=(len(devs) == 1)):
            c1, c2 = st.columns(2)
            c1.metric(t("device.state", lang), state)
            c2.metric("📞", phone)
            if state != "logged_in":
                st.warning(t("device.disconnected_note", lang))
            b1, b2, b3, b4 = st.columns(4)
            if b1.button(t("device.check_status", lang), key=f"chk_{d['id']}"):
                if info["ok"] and info.get("state") == "logged_in":
                    run_async(database.touch_device_login(gid))
                st.rerun()
            if not d["is_default"]:
                if b2.button(t("device.set_default", lang), key=f"def_{d['id']}"):
                    run_async(database.set_default_device(uid, gid))
                    st.rerun()
            else:
                b2.caption(t("device.is_default", lang))
            if b3.button(t("device.unlink", lang), key=f"unl_{d['id']}"):
                gowa_device_logout(gid)
                run_async(database.remove_user_device(uid, gid))
                st.success(t("device.unlinked", lang))
                st.rerun()
            if b4.button(t("device.delete", lang), key=f"deld_{d['id']}"):
                gowa_device_delete(gid)
                run_async(database.remove_user_device(uid, gid))
                st.success(t("device.deleted", lang))
                st.rerun()

    if len(devs) < max_dev:
        st.caption(t("device.auto_cleanup_note", lang))
        claimed = run_async(database.get_claimed_device_ids())
        gdevs = gowa_list_devices()
        unclaimed = [x for x in gdevs if x.get("id") not in claimed]
        if unclaimed:
            with st.expander(t("device.claim_title", lang)):
                st.caption(t("device.claim_note", lang))
                for x in unclaimed:
                    cc1, cc2 = st.columns([3, 1])
                    xlabel = x.get("display_name") or x.get("id")
                    xstate = "🟢" if x.get("state") == "logged_in" else "⚪"
                    cc1.write(f"{xstate} **{xlabel}**\n\n`{x.get('id')}`")
                    if cc2.button(t("device.use", lang), key=f"claim_{x.get('id')}"):
                        if x.get("state") == "logged_in":
                            _claim_success(x.get("id"), xlabel)
                        else:
                            gowa_device_delete(x.get("id"))
                            res = gowa_create_device(user["username"])
                            if res["ok"]:
                                st.session_state.pair_device_id = res["id"]
                                st.session_state.pair_device_name = user["username"]
                                st.rerun()
                            else:
                                st.error(t("device.create_failed", lang).format(
                                    e=res["error"]))
        with st.expander(t("device.add", lang), expanded=(len(devs) == 0)):
            st.caption(t("device.auto_name", lang).format(n=user["username"]))
            if st.button(t("device.add", lang), type="primary"):
                res = gowa_create_device(user["username"])
                if res["ok"]:
                    st.session_state.pair_device_id = res["id"]
                    st.session_state.pair_device_name = user["username"]
                    st.rerun()
                else:
                    st.error(t("device.create_failed", lang).format(e=res["error"]))
    else:
        st.info(t("device.max_reached", lang).format(n=max_dev))



# -- Test Message Page -----------------------------------------

elif page == "test":
    st.title(t("dash.test_title", lang))
    if _need_device():
        st.stop()

    phone = st.text_input(t("dash.phone_intl", lang), placeholder=t("dash.phone_ph", lang))
    message = st.text_area(t("dash.message_label", lang), placeholder=t("dash.test_msg_ph", lang))

    if st.button(t("dash.send_btn2", lang), type="primary", disabled=not phone or not message):
        with st.spinner(t("dash.sending", lang)):
            result = run_async(sender.send_single(uid, phone, message, device_id=device_id))
        if result["status"] == "sent":
            st.success(t("dash.msg_sent_sid", lang).format(sid=result.get("sid", "N/A")))
        else:
            st.error(t("dash.failed_err", lang).format(err=result.get("error", "Unknown")))


# -- Configuration Page ----------------------------------------

elif page == "config":
    st.title(t("dash.config_title", lang))
    warnings = config.validate(lang)
    if warnings:
        for w in warnings:
            st.warning(w)
    else:
        st.success(t("dash.config_ok", lang))
    st.json({
        t("dash.cfg_bot", lang): "Wasend",
        t("dash.cfg_version", lang): config.BOT_VERSION,
        t("dash.cfg_provider", lang): config.WA_PROVIDER,
        t("dash.cfg_delay_range", lang): f"{config.GOWA_DELAY_MIN_SECONDS:g}–{config.GOWA_DELAY_MAX_SECONDS:g}s",
        t("dash.cfg_database", lang): config.DB_PATH,
    })


# -- Admin Page ------------------------------------------------

elif page == "admin" and is_admin:
    st.title(t("admin.title", lang))

    with st.expander(t("admin.create_user", lang)):
        nu = st.text_input(t("admin.username", lang), key="new_username")
        npw = st.text_input(t("admin.password", lang), type="password", key="new_password")
        if st.button(t("admin.create_btn", lang)) and nu and npw:
            new_id = run_async(database.create_user(nu, npw))
            if new_id:
                st.success(t("admin.created", lang).format(u=nu))
                st.rerun()
            else:
                st.error(t("admin.exists", lang))

    users = run_async(database.get_all_users())
    _gmap = {d.get("id"): d for d in gowa_list_devices()}
    for u in users:
        with st.expander(f"{'👑' if u['is_admin'] else '👤'} {u['username']}"):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**{t('admin.col_device', lang)}:** {u['device_count']} / {database.MAX_DEVICES_PER_USER}")
            c2.markdown(f"**{t('admin.col_created', lang)}:** {(u['created_at'] or '')[:10]}")
            _udevs = run_async(database.get_user_devices(u["id"]))
            if _udevs:
                _dlines = []
                for _dd in _udevs:
                    _st8 = (_gmap.get(_dd["gowa_device_id"]) or {}).get("state", "?")
                    _star = "⭐" if _dd["is_default"] else ""
                    _dlines.append(f"{_star}{_dd['device_name'] or _dd['gowa_device_id']} ({_st8})")
                st.markdown(f"**{t('admin.user_devices', lang)}** " + ", ".join(_dlines))
            _ucamps = run_async(database.get_all_campaigns(u["id"]))
            if _ucamps:
                st.markdown(f"**{t('admin.user_campaigns', lang)}**")
                for _cc in _ucamps:
                    _cc1, _cc2 = st.columns([4, 1])
                    _cc1.text(f"{_cc['name']} [{_cc['status']}] ({_cc.get('contact_count', 0)})")
                    if _cc2.button(t("admin.delete_campaign", lang), key=f"delc_{_cc['id']}"):
                        run_async(database.delete_campaign(_cc["id"]))
                        st.success(t("admin.campaign_deleted", lang))
                        st.rerun()
            else:
                st.caption(t("admin.no_campaigns", lang))
            b1, b2 = st.columns(2)
            with b1:
                with st.popover(t("admin.reset_pw", lang)):
                    pw2 = st.text_input(t("admin.new_password", lang), type="password",
                                        key=f"pw_{u['id']}")
                    if st.button(t("admin.create_btn", lang), key=f"pwbtn_{u['id']}") and pw2:
                        run_async(database.update_user_password(u["id"], pw2))
                        st.success(t("admin.pw_updated", lang))
            with b2:
                if not u["is_admin"] and u["id"] != uid:
                    if st.button(t("admin.monitor", lang), key=f"mon_{u['id']}"):
                        st.session_state.real_admin = st.session_state.user
                        st.session_state.user = run_async(database.get_user(u["id"]))
                        st.session_state.impersonating = True
                        st.rerun()
                    if st.button(t("admin.delete", lang), key=f"delu_{u['id']}"):
                        for dd in run_async(database.get_user_devices(u["id"])):
                            gowa_device_delete(dd["gowa_device_id"])
                        run_async(database.delete_user(u["id"]))
                        st.success(t("admin.deleted", lang))
                        st.rerun()
                elif u["id"] == uid:
                    st.caption(t("admin.cannot_delete_self", lang))

    st.subheader(t("admin.unclaimed_title", lang))
    _claimed = run_async(database.get_claimed_device_ids())
    _unclaimed = [d for d in _gmap.values() if d.get("id") not in _claimed]
    if not _unclaimed:
        st.caption(t("admin.none_unclaimed", lang))
    for _ud in _unclaimed:
        _ulabel = _ud.get("display_name") or _ud.get("id")
        _ustate = _ud.get("state", "?")
        _uicon = "🟢" if _ustate == "logged_in" else "⚪"
        with st.expander(f"{_uicon} {_ulabel} — {_ustate}"):
            _target = st.selectbox(
                t("admin.assign_to", lang),
                [u["username"] for u in users],
                key=f"assign_{_ud.get('id')}",
            )
            if st.button(t("admin.assign_btn", lang), key=f"assignbtn_{_ud.get('id')}"):
                _tuser = next(u for u in users if u["username"] == _target)
                _gid = _ud.get("id")
                try:
                    if _ustate == "logged_in":
                        run_async(database.add_user_device(_tuser["id"], _gid, _ulabel))
                        run_async(database.touch_device_login(_gid))
                    else:
                        gowa_device_delete(_gid)
                        _res = gowa_create_device(_tuser["username"])
                        if not _res["ok"]:
                            st.error(t("device.create_failed", lang).format(
                                e=_res["error"]))
                            st.stop()
                        run_async(database.add_user_device(
                            _tuser["id"], _res["id"], _tuser["username"]))
                        _ulabel = _tuser["username"]
                    st.success(t("admin.assigned", lang).format(d=_ulabel, u=_target))
                    st.rerun()
                except ValueError:
                    st.error(t("device.max_reached", lang).format(
                        n=database.MAX_DEVICES_PER_USER))
            if st.button(t("admin.delete_device", lang), key=f"deld_{_ud.get('id')}"):
                gowa_device_delete(_ud.get("id"))
                st.success(t("admin.device_deleted", lang))
                st.rerun()
