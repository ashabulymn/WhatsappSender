"""
Wasend — Streamlit Web Dashboard (multi-user).

- Login username/password (tabel users), admin bisa lihat semua data
- Perangkat WhatsApp per user (QR pairing via GoWA)
- Kirim campaign: varian pesan acak + jeda random (anti-ban) + jadwal
- Scheduler background untuk pesan terjadwal
- Bilingual UI (Indonesian / English) via i18n module.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

import config
import database
import csv_handler
import sender
from i18n import t, available_languages
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
    device_id = user.get("gowa_device_id") or ""
    if not device_id:
        run_async(database.update_scheduled_status(s["id"], "failed", "no device linked"))
        return
    try:
        if s.get("campaign_id"):
            result = run_async(sender.send_campaign(
                user_id=s["user_id"],
                campaign_id=s["campaign_id"],
                delay_min=s.get("delay_min") or config.GOWA_DELAY_MIN_SECONDS,
                delay_max=s.get("delay_max") or config.GOWA_DELAY_MAX_SECONDS,
                device_id=device_id,
            ))
            err = "" if result["failed"] == 0 else f"{result['failed']} failed"
            run_async(database.update_scheduled_status(
                s["id"], "sent" if result["failed"] == 0 else "failed", err))
        else:
            result = run_async(sender.send_single(
                user_id=s["user_id"], phone=s["phone"], content=s["content"],
                device_id=device_id))
            run_async(database.update_scheduled_status(
                s["id"], result["status"], result.get("error", "")))
    except Exception as e:
        logger.error(f"Scheduler failed for #{s['id']}: {e}")
        run_async(database.update_scheduled_status(s["id"], "failed", str(e)[:200]))


def _scheduler_loop():
    while True:
        try:
            due = run_async(database.get_due_scheduled(50))
            for s in due:
                _process_scheduled(s)
        except Exception as e:
            logger.error(f"Scheduler tick failed: {e}")
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

# -- Language --------------------------------------------------
if "lang" not in st.session_state:
    st.session_state.lang = config.APP_LANG
lang = st.session_state.lang

# -- Authentication Gate ---------------------------------------

init_db()
start_scheduler()


def _refresh_user():
    st.session_state.user = run_async(database.get_user(st.session_state.user["id"]))


if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title(t("auth.login_title", lang))
    username = st.text_input(t("auth.username", lang))
    password = st.text_input(t("auth.password", lang), type="password")
    if st.button(t("auth.login_btn", lang)):
        u = run_async(database.get_user_by_username(username))
        if u and database.verify_password(password, u["password_hash"]):
            st.session_state.user = u
            st.rerun()
        else:
            st.error(t("auth.wrong", lang))
    st.stop()

user = st.session_state.user
uid = user["id"]
is_admin = bool(user.get("is_admin"))
device_id = user.get("gowa_device_id") or ""

# -- Sidebar ---------------------------------------------------

with st.sidebar:
    st.title("📱 Wasend")
    st.caption(f"v{config.BOT_VERSION} | {config.WA_PROVIDER.upper()}")
    st.markdown(t("auth.welcome", lang).format(u=user["username"]))
    if is_admin:
        view_all = st.checkbox(t("admin.view_all", lang), key="view_all")
    else:
        view_all = False
    if st.button(t("auth.logout", lang)):
        st.session_state.user = None
        st.rerun()

    # Language toggle
    lang_codes = [code for code, _ in available_languages()]
    lang_labels = [label for _, label in available_languages()]
    current_idx = lang_codes.index(lang) if lang in lang_codes else 0
    picked = st.selectbox(t("dash.lang_label", lang), lang_labels,
                          index=current_idx, key="lang_picker")
    new_lang = lang_codes[lang_labels.index(picked)]
    if new_lang != lang:
        st.session_state.lang = new_lang
        st.rerun()
    lang = st.session_state.lang

    pages = [
        ("dashboard", t("dash.page_dashboard", lang)),
        ("import", t("dash.page_import", lang)),
        ("send", t("dash.page_send", lang)),
        ("schedule", t("dash.page_schedule", lang)),
        ("contacts", t("dash.page_contacts", lang)),
        ("stats", t("dash.page_stats", lang)),
        ("templates", t("dash.page_templates", lang)),
        ("device", t("dash.page_device", lang)),
        ("test", t("dash.page_test", lang)),
        ("config", t("dash.page_config", lang)),
    ]
    if is_admin:
        pages.append(("admin", t("dash.page_admin", lang)))
    labels = [label for _, label in pages]
    choice = st.radio(t("dash.nav", lang), labels)
    page = pages[labels.index(choice)][0]

    st.divider()
    st.markdown(f"[GitHub]({config.COMMUNITY_URL}) | [Site]({config.WEBSITE_URL})")

# view_uid: None = admin melihat semua
view_uid = None if (is_admin and view_all) else uid


def _need_device() -> bool:
    """Warn if user has no linked device. Returns True when blocked."""
    if not device_id:
        st.warning(t("device.need_device", lang))
        return True
    return False


# -- Dashboard Page --------------------------------------------

if page == "dashboard":
    st.title(t("dash.page_dashboard", lang))

    contact_count = run_async(database.get_contact_count(view_uid))
    campaigns = run_async(database.get_all_campaigns(view_uid))
    stats = run_async(database.get_send_stats(view_uid))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("dash.metric_contacts", lang), contact_count)
    col2.metric(t("dash.metric_campaigns", lang), len(campaigns))
    col3.metric(t("dash.metric_sent", lang), stats.get("sent", 0) + stats.get("delivered", 0))
    col4.metric(t("dash.metric_failed", lang), stats.get("failed", 0))

    total_out = sum(stats.get(s, 0) for s in ("sent", "delivered", "read", "failed"))
    delivered = stats.get("delivered", 0) + stats.get("read", 0)
    rate = (delivered / total_out * 100) if total_out > 0 else 0

    col5, col6, col7, col8 = st.columns(4)
    col5.metric(t("dash.metric_delivery_rate", lang), f"{rate:.1f}%")
    col6.metric(t("dash.metric_delivered", lang), stats.get("delivered", 0))
    col7.metric(t("dash.metric_read", lang), stats.get("read", 0))
    col8.metric(t("dash.metric_total", lang), stats.get("total", 0))

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

    contacts = run_async(database.get_contacts_for_campaign(campaign_id))
    st.info(t("dash.contacts_in_this_campaign", lang).format(n=len(contacts)))

    # --- Message variants ---
    st.subheader(t("send.variants_label", lang))
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

    if st.button(t("send.preview_random", lang)) and variants_input and contacts:
        sample = contacts[0]
        v = sender.pick_variant([{"body": b, "weight": 1} for b in variants_input])
        st.text(sender.render_message(v["body"], sample))

    # --- Random delay range ---
    st.subheader(t("send.delay_label", lang))
    dcol1, dcol2 = st.columns(2)
    delay_min = dcol1.number_input(t("send.delay_min", lang), min_value=1.0, value=15.0, step=1.0)
    delay_max = dcol2.number_input(t("send.delay_max", lang), min_value=1.0, value=30.0, step=1.0)
    if delay_max < delay_min:
        delay_max = delay_min

    # --- Send now or schedule ---
    schedule_it = st.checkbox(t("send.schedule_toggle", lang))
    sched_dt = None
    if schedule_it:
        scol1, scol2 = st.columns(2)
        sdate = scol1.date_input(t("sched.datetime", lang), value=datetime.now().date())
        stime = scol2.time_input("", value=(datetime.now() + timedelta(hours=1)).time())
        sched_dt = datetime.combine(sdate, stime)

    btn_label = t("sched.schedule_btn", lang) if schedule_it else t("dash.send_btn", lang)
    if st.button(btn_label, type="primary", disabled=not variants_input or not contacts):
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
                    delay_min=delay_min, delay_max=delay_max))
                # store delay on the schedule row
                st.success(t("sched.scheduled", lang).format(t=sched_dt.strftime("%d %b %Y %H:%M")))
        else:
            # save variants
            for v in run_async(database.get_variants(uid, campaign_id)):
                run_async(database.delete_variant(v["id"], uid))
            db_variants = []
            for b in variants_input:
                vid = run_async(database.create_variant(uid, b, campaign_id))
                db_variants.append({"id": vid, "body": b, "weight": 1})
            progress = st.progress(0)
            status_text = st.empty()

            def _on_progress(i, total, phone):
                progress.progress(i / total)
                status_text.text(t("dash.sending_progress", lang).format(i=i, total=total, s="", f=""))

            result = run_async(sender.send_campaign(
                user_id=uid, campaign_id=campaign_id, variants=db_variants,
                delay_min=delay_min, delay_max=delay_max, device_id=device_id,
                on_progress=_on_progress,
            ))
            st.success(t("dash.done_sent_failed", lang).format(s=result["sent"], f=result["failed"]))


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
            cols = st.columns([3, 2, 2, 1])
            cols[0].text(f"{s['phone'] or ''} {desc}")
            cols[1].text(s["scheduled_at"])
            cols[2].text(s["status"])
            if cols[3].button(t("sched.cancel", lang), key=f"cancel_{s['id']}"):
                run_async(database.cancel_scheduled(s["id"], view_uid))
                st.success(t("sched.cancelled", lang))
                st.rerun()


# -- Contacts Page ---------------------------------------------

elif page == "contacts":
    st.title(t("dash.contacts_title", lang))
    contacts = run_async(database.get_all_contacts(view_uid))
    if not contacts:
        st.info(t("dash.no_contacts_import_csv", lang))
    else:
        st.metric(t("dash.total_contacts", lang), len(contacts))
        df = pd.DataFrame(contacts)
        display_cols = [c for c in ["phone", "first_name", "last_name", "email", "created_at"] if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True)


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

    if device_id:
        info = gowa_device_info(device_id)
        if info["ok"]:
            st.success(t("device.linked", lang).format(n=info.get("name") or device_id))
            c1, c2 = st.columns(2)
            c1.metric(t("device.state", lang), info.get("state", "?"))
            c2.metric("📞", info.get("phone") or "-")
            b1, b2, b3 = st.columns(3)
            if b1.button(t("device.check_status", lang)):
                st.rerun()
            if b2.button(t("device.unlink", lang)):
                gowa_device_logout(device_id)
                run_async(database.set_user_device(uid, "", ""))
                _refresh_user()
                st.success(t("device.unlinked", lang))
                st.rerun()
            if b3.button(t("device.delete", lang)):
                gowa_device_delete(device_id)
                run_async(database.set_user_device(uid, "", ""))
                _refresh_user()
                st.success(t("device.deleted", lang))
                st.rerun()
        else:
            st.warning(info.get("error", "?"))
            if st.button(t("device.delete", lang)):
                run_async(database.set_user_device(uid, "", ""))
                _refresh_user()
                st.rerun()
    else:
        st.info(t("device.none", lang))
        # Claim an existing GoWA device slot
        claimed = run_async(database.get_claimed_device_ids())
        gdevs = gowa_list_devices()
        unclaimed = [d for d in gdevs.get("devices", [])
                     if d.get("id") not in claimed]
        if unclaimed:
            with st.expander(t("device.claim_title", lang)):
                for d in unclaimed:
                    cc1, cc2 = st.columns([3, 1])
                    label = d.get("name") or d.get("id")
                    state = "🟢" if d.get("state") == "logged_in" else "⚪"
                    cc1.write(f"{state} **{label}**\n\n`{d.get('id')}`")
                    if cc2.button(t("device.use", lang), key=f"claim_{d.get('id')}"):
                        run_async(database.set_user_device(uid, d.get("id"), label))
                        _refresh_user()
                        st.success(t("device.linked", lang).format(n=label))
                        st.rerun()
        with st.expander(t("device.add", lang), expanded=True):
            st.caption(t("device.auto_name", lang).format(n=user["username"]))
            if st.button(t("device.add", lang), type="primary"):
                res = gowa_create_device(user["username"])
                if res["ok"]:
                    st.session_state.pair_device_id = res["id"]
                    st.session_state.pair_device_name = user["username"]
                    st.rerun()
                else:
                    st.error(t("device.create_failed", lang).format(e=res["error"]))

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
                            run_async(database.set_user_device(
                                uid, pair_id, st.session_state.get("pair_device_name", "")))
                            st.session_state.pop("pair_device_id", None)
                            _refresh_user()
                            st.success(t("device.linked", lang).format(
                                n=info.get("name") or pair_id))
                            st.rerun()
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
                            run_async(database.set_user_device(
                                uid, pair_id, st.session_state.get("pair_device_name", "")))
                            st.session_state.pop("pair_device_id", None)
                            st.session_state.pop("pair_code", None)
                            _refresh_user()
                            st.success(t("device.linked", lang).format(
                                n=info.get("name") or pair_id))
                            st.rerun()
                        else:
                            st.info(f"{t('device.state', lang)}: {info.get('state', '?')}")


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
    for u in users:
        with st.expander(f"{'👑' if u['is_admin'] else '👤'} {u['username']}"):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**{t('admin.col_device', lang)}:** {u['gowa_device_id'][:8] + '…' if u['gowa_device_id'] else '-'}")
            c2.markdown(f"**{t('admin.col_created', lang)}:** {(u['created_at'] or '')[:10]}")
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
                    if st.button(t("admin.delete", lang), key=f"delu_{u['id']}"):
                        if u["gowa_device_id"]:
                            gowa_device_delete(u["gowa_device_id"])
                        run_async(database.delete_user(u["id"]))
                        st.success(t("admin.deleted", lang))
                        st.rerun()
                elif u["id"] == uid:
                    st.caption(t("admin.cannot_delete_self", lang))
