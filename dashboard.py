"""
WhatsApp Bulk Sender — Streamlit Web Dashboard
Web interface for importing contacts, sending campaigns, and viewing stats.
Bilingual UI (Indonesian / English) via i18n module.
"""

import asyncio
import hmac
import threading

import streamlit as st
import pandas as pd

import config
import database
import csv_handler
from i18n import t, available_languages
from whatsapp import WhatsAppClient, _render_message


# -- Async Bridge ----------------------------------------------

_loop = asyncio.new_event_loop()
_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_thread.start()


def run_async(coro):
    """Run an async coroutine from sync Streamlit context."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=30)
    except TimeoutError:
        future.cancel()
        raise RuntimeError("Database operation timed out")


def init_db():
    if "db_init" not in st.session_state:
        run_async(database.init())
        st.session_state.db_init = True


# -- Page Config -----------------------------------------------

st.set_page_config(
    page_title=config.BOT_NAME,
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- Language --------------------------------------------------
# Default from APP_LANG env; user can switch per-session in the sidebar.
if "lang" not in st.session_state:
    st.session_state.lang = config.APP_LANG
lang = st.session_state.lang

# -- Authentication Gate ---------------------------------------

if config.DASHBOARD_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title(t("dash.auth_title", lang))
        st.markdown(t("dash.auth_prompt", lang))
        password = st.text_input(t("dash.auth_password", lang), type="password")
        if st.button(t("dash.auth_login", lang)):
            if hmac.compare_digest(password, config.DASHBOARD_PASSWORD):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error(t("dash.auth_wrong", lang))
        st.stop()

init_db()


# -- Sidebar ---------------------------------------------------

with st.sidebar:
    st.title(t("dash.sidebar_title", lang))
    st.caption(f"v{config.BOT_VERSION} | {config.WA_PROVIDER.upper()}")

    # Language toggle
    lang_codes = [code for code, _ in available_languages()]
    lang_labels = [label for _, label in available_languages()]
    current_idx = lang_codes.index(lang) if lang in lang_codes else 0
    picked = st.selectbox(
        t("dash.lang_label", lang),
        lang_labels,
        index=current_idx,
        key="lang_picker",
    )
    new_lang = lang_codes[lang_labels.index(picked)]
    if new_lang != lang:
        st.session_state.lang = new_lang
        st.rerun()
    lang = st.session_state.lang

    pages = [
        ("dashboard", t("dash.page_dashboard", lang)),
        ("import", t("dash.page_import", lang)),
        ("send", t("dash.page_send", lang)),
        ("contacts", t("dash.page_contacts", lang)),
        ("stats", t("dash.page_stats", lang)),
        ("templates", t("dash.page_templates", lang)),
        ("test", t("dash.page_test", lang)),
        ("config", t("dash.page_config", lang)),
    ]
    labels = [label for _, label in pages]
    choice = st.radio(t("dash.nav", lang), labels)
    page = pages[labels.index(choice)][0]

    st.divider()
    st.markdown(f"[GitHub]({config.COMMUNITY_URL}) | [Site]({config.WEBSITE_URL})")


# -- Dashboard Page --------------------------------------------

if page == "dashboard":
    st.title(t("dash.page_dashboard", lang))

    contact_count = run_async(database.get_contact_count())
    campaigns = run_async(database.get_all_campaigns())
    stats = run_async(database.get_send_stats())

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

    # Recent campaigns
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
                        ))
                        contact_ids.append(cid)

                    campaign_id = run_async(database.create_campaign(name=campaign_name, message=""))
                    run_async(database.add_contacts_to_campaign(campaign_id, contact_ids))

                st.success(t("dash.imported_n", lang).format(n=len(contacts), name=campaign_name))

                # Preview
                df = pd.DataFrame(contacts)
                st.dataframe(df.head(20), use_container_width=True)
            else:
                st.error(t("dash.no_valid_contacts", lang))

    st.divider()
    st.subheader(t("dash.csv_format_title", lang))
    st.code("phone,first_name,last_name,email\n+6281234567890,Budi,Santoso,budi@email.com\n+6289876543210,Siti,Aminah,", language="csv")


# -- Send Page -------------------------------------------------

elif page == "send":
    st.title(t("dash.send_title", lang))

    campaigns = run_async(database.get_all_campaigns())
    if not campaigns:
        st.warning(t("dash.no_campaign_import_first", lang))
        st.stop()

    # Select campaign
    campaign_options = {
        f"{c['name']} ({c.get('contact_count', 0)} {t('dash.contacts_suffix', lang)}) [{c['status']}]": c["id"]
        for c in campaigns
    }
    selected = st.selectbox(t("dash.campaign_label", lang), list(campaign_options.keys()))
    campaign_id = campaign_options[selected]

    contacts = run_async(database.get_contacts_for_campaign(campaign_id))
    st.info(t("dash.contacts_in_this_campaign", lang).format(n=len(contacts)))

    # Template selector
    templates = run_async(database.get_all_templates())
    write_msg = t("dash.write_message", lang)
    template_names = [write_msg] + [t["name"] for t in templates]
    selected_tpl = st.selectbox(t("dash.template_label", lang), template_names)

    default_msg = ""
    if selected_tpl != write_msg:
        tpl = next((t for t in templates if t["name"] == selected_tpl), None)
        if tpl:
            default_msg = tpl["body"]

    message = st.text_area(
        t("dash.message_label", lang),
        value=default_msg,
        height=200,
        help=t("dash.placeholders_help", lang),
    )

    if message and contacts:
        sample = contacts[0]
        preview = _render_message(message, {
            "first_name": sample.get("first_name", "Contact"),
            "last_name": sample.get("last_name", ""),
            "phone": sample.get("phone", ""),
            "name": f"{sample.get('first_name', '')} {sample.get('last_name', '')}".strip(),
        })
        st.markdown(t("dash.preview_label", lang))
        st.text(preview)

    col1, col2 = st.columns(2)
    dry_run = col1.checkbox(t("dash.dry_run", lang), value=True)

    if col2.button(t("dash.send_btn", lang), type="primary", disabled=not message or not contacts):
        if dry_run:
            st.warning(t("dash.dry_run_msg", lang).format(n=len(contacts)))
        else:
            run_async(database.update_campaign_message(campaign_id, message))
            wa = WhatsAppClient()
            recipients = [
                {
                    "phone": c["phone"],
                    "first_name": c.get("first_name", "Contact"),
                    "last_name": c.get("last_name", ""),
                    "id": c.get("id"),
                }
                for c in contacts
            ]

            progress = st.progress(0)
            status_text = st.empty()

            sent = 0
            failed = 0
            for i, r in enumerate(recipients):
                body = _render_message(message, {
                    "first_name": r.get("first_name", "Contact"),
                    "last_name": r.get("last_name", ""),
                    "phone": r.get("phone", ""),
                    "name": f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
                })
                result = run_async(wa.send_message(r["phone"], body))
                run_async(database.log_message(
                    phone=r["phone"],
                    content=body[:500],
                    status=result["status"],
                    campaign_id=campaign_id,
                    contact_id=r.get("id"),
                    wa_message_id=result.get("sid", ""),
                    error_message=result.get("error", ""),
                ))
                if result["status"] == "sent":
                    sent += 1
                else:
                    failed += 1
                progress.progress((i + 1) / len(recipients))
                status_text.text(t("dash.sending_progress", lang).format(
                    i=i + 1, total=len(recipients), s=sent, f=failed))

            run_async(database.update_campaign_status(campaign_id, "sent"))
            st.success(t("dash.done_sent_failed", lang).format(s=sent, f=failed))


# -- Contacts Page ---------------------------------------------

elif page == "contacts":
    st.title(t("dash.contacts_title", lang))

    contacts = run_async(database.get_all_contacts())

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

    stats = run_async(database.get_send_stats())

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
        campaigns = run_async(database.get_all_campaigns())
        if campaigns:
            for c in campaigns[:5]:
                camp_stats = run_async(database.get_campaign_stats(c["id"]))
                total = sum(camp_stats.values())
                delivered = camp_stats.get("delivered", 0) + camp_stats.get("read", 0)
                rate = (delivered / total * 100) if total > 0 else 0
                st.markdown(f"**{c['name']}** — {c['created_at'][:10]} — {t('dash.rate_n', lang).format(rate=f'{rate:.0f}')}")
        else:
            st.caption(t("dash.no_campaigns", lang))

    # Recent messages
    st.divider()
    st.subheader(t("dash.recent_messages", lang))
    messages = run_async(database.get_recent_messages(20))
    if messages:
        df_msg = pd.DataFrame(messages)
        display_cols = [c for c in ["phone", "status", "content", "sent_at"] if c in df_msg.columns]
        st.dataframe(df_msg[display_cols], use_container_width=True)
    else:
        st.caption(t("dash.no_messages", lang))


# -- Templates Page --------------------------------------------

elif page == "templates":
    st.title(t("dash.templates_title", lang))

    templates = run_async(database.get_all_templates())

    with st.expander(t("dash.create_template", lang)):
        name = st.text_input(t("dash.template_name", lang))
        category = st.selectbox(t("dash.category", lang), ["general", "broadcast", "info"])
        body = st.text_area(
            t("dash.content", lang),
            height=150,
            help=t("dash.placeholders_help", lang),
        )
        if st.button(t("dash.create_btn", lang)) and name and body:
            run_async(database.create_template(name, category, body))
            st.success(t("dash.template_created", lang).format(name=name))
            st.rerun()

    for t in templates:
        with st.expander(f"📄 {t['name']} [{t['category']}]"):
            st.text(t["body"])
            if st.button(f"{t('dash.delete_label', lang)} {t['name']}", key=f"del_{t['name']}"):
                run_async(database.delete_template(t["name"]))
                st.success(t("dash.template_deleted", lang).format(name=t["name"]))
                st.rerun()


# -- Test Message Page -----------------------------------------

elif page == "test":
    st.title(t("dash.test_title", lang))

    phone = st.text_input(
        t("dash.phone_intl", lang),
        placeholder=t("dash.phone_ph", lang),
    )
    message = st.text_area(t("dash.message_label", lang), placeholder=t("dash.test_msg_ph", lang))

    if st.button(t("dash.send_btn2", lang), type="primary", disabled=not phone or not message):
        wa = WhatsAppClient()
        with st.spinner(t("dash.sending", lang)):
            result = run_async(wa.send_message(phone, message))
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
        t("dash.cfg_bot", lang): config.BOT_NAME,
        t("dash.cfg_version", lang): config.BOT_VERSION,
        t("dash.cfg_provider", lang): config.WA_PROVIDER,
        t("dash.cfg_webhook_port", lang): config.WEBHOOK_PORT,
        t("dash.cfg_rate_limit", lang): (
            f"{config.GOWA_SEND_INTERVAL_SECONDS:g}s / msg"
            if config.WA_PROVIDER == "gowa"
            else f"{config.WA_MESSAGES_PER_SECOND} msg/s"
        ),
        t("dash.cfg_database", lang): config.DB_PATH,
    })
