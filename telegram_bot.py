"""
WhatsApp Bulk Sender — Telegram Admin Bot
Remote administration via Telegram: import CSV, send campaigns, stats.
"""

import asyncio
import html
import logging
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

import config
import database
import csv_handler
from whatsapp import WhatsAppClient, _render_message
from i18n import t

logger = logging.getLogger(__name__)
wa_client = WhatsAppClient()
# Operating user for bot single-user mode (resolved to admin at startup)
BOT_UID = 1
BOT_DEVICE = ""


async def _resolve_bot_user():
    global BOT_UID, BOT_DEVICE, wa_client
    admin = await database.get_admin_user()
    if admin:
        BOT_UID = admin["id"]
        _dev = await database.get_default_device(admin["id"])
        BOT_DEVICE = _dev["gowa_device_id"] if _dev else ""
        wa_client = WhatsAppClient(device_id=BOT_DEVICE)

LANG = config.APP_LANG
# Language-independent yes-words (id + en + legacy fr)
CONFIRM_WORDS = {"ya", "y", "iya", "yes", "ok", "oui"}

# Conversation states
IMPORT_CSV, IMPORT_NAME = range(2)
SEND_CAMPAIGN, SEND_MESSAGE, SEND_CONFIRM = range(10, 13)


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return html.escape(str(text))


# -- Auth Decorator --------------------------------------------

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in config.TELEGRAM_ADMIN_IDS:
            if update.message:
                await update.message.reply_text(t("tg.access_denied", lang=LANG))
            elif update.callback_query:
                await update.callback_query.answer(t("tg.access_denied", lang=LANG), show_alert=True)
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def _reply(update: Update):
    """Get the appropriate reply method (message or callback query)."""
    if update.message:
        return update.message.reply_text
    if update.callback_query:
        return update.callback_query.edit_message_text
    return None


# -- Commands --------------------------------------------------

@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = _reply(update)
    if not reply:
        return
    keyboard = [
        [
            InlineKeyboardButton(t("tg.btn_stats", lang=LANG), callback_data="stats"),
            InlineKeyboardButton(t("tg.btn_contacts", lang=LANG), callback_data="contacts"),
        ],
        [
            InlineKeyboardButton(t("tg.btn_campaigns", lang=LANG), callback_data="campaigns"),
            InlineKeyboardButton(t("tg.btn_templates", lang=LANG), callback_data="templates"),
        ],
        [
            InlineKeyboardButton(t("tg.btn_help", lang=LANG), callback_data="help"),
        ],
    ]
    await reply(
        f"📱 <b>{_esc(config.BOT_NAME)}</b> v{config.BOT_VERSION}\n\n"
        f"{t('tg.start_admin', lang=LANG)}\n"
        f"{t('tg.provider', lang=LANG).format(p=config.WA_PROVIDER.upper())}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = _reply(update)
    if not reply:
        return
    contact_count = await database.get_contact_count()
    stats = await database.get_send_stats()
    campaigns = await database.get_all_campaigns()

    text = (
        f"{t('tg.stats_title', lang=LANG)}\n\n"
        f"{t('tg.contacts_l', lang=LANG).format(n=contact_count)}\n"
        f"{t('tg.campaigns_l', lang=LANG).format(n=len(campaigns))}\n"
        f"{t('tg.total_sends', lang=LANG).format(n=stats.get('total', 0))}\n\n"
        f"{t('tg.statuses', lang=LANG)}\n"
        f"  {t('tg.pending', lang=LANG).format(n=stats.get('queued', 0))}\n"
        f"  {t('tg.sent', lang=LANG).format(n=stats.get('sent', 0))}\n"
        f"  {t('tg.delivered', lang=LANG).format(n=stats.get('delivered', 0))}\n"
        f"  {t('tg.read', lang=LANG).format(n=stats.get('read', 0))}\n"
        f"  {t('tg.failed', lang=LANG).format(n=stats.get('failed', 0))}"
    )
    await reply(text, parse_mode="HTML")


@admin_only
async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = _reply(update)
    if not reply:
        return
    contacts = await database.get_all_contacts()
    if not contacts:
        await reply(t("tg.no_contacts_use_import", lang=LANG))
        return

    lines = [f"{t('tg.contacts_title', lang=LANG).format(n=len(contacts))}\n"]
    for c in contacts[:30]:
        fn = _esc(c.get("first_name", ""))
        ln = _esc(c.get("last_name", ""))
        lines.append(f"• <code>{_esc(c['phone'])}</code> — {fn} {ln}")

    if len(contacts) > 30:
        lines.append(t("tg.and_more", lang=LANG).format(n=len(contacts) - 30))

    await reply("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = _reply(update)
    if not reply:
        return
    templates = await database.get_all_templates()
    if not templates:
        await reply(t("tg.no_templates", lang=LANG))
        return

    lines = [f"{t('tg.templates_title', lang=LANG).format(n=len(templates))}\n"]
    for tpl in templates:
        lines.append(f"• <b>{_esc(tpl['name'])}</b> [{_esc(tpl['category'])}]\n  {_esc(tpl['body'][:60])}...")

    await reply("\n".join(lines), parse_mode="HTML")


# -- Import CSV Conversation -----------------------------------

@admin_only
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    await update.message.reply_text(
        f"{t('tg.import_title', lang=LANG)}\n\n"
        f"{t('tg.import_instructions', lang=LANG)}",
        parse_mode="HTML",
    )
    return IMPORT_CSV


async def import_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.TELEGRAM_ADMIN_IDS:
        return ConversationHandler.END

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text(t("tg.send_csv", lang=LANG))
        return IMPORT_CSV

    file = await doc.get_file()
    data = await file.download_as_bytearray()
    contacts, errors = csv_handler.parse_csv(bytes(data), lang=LANG)

    if not contacts:
        err_text = "\n".join(errors[:3]) if errors else t("tg.no_valid", lang=LANG)
        await update.message.reply_text(t("tg.import_failed", lang=LANG).format(err=err_text))
        return ConversationHandler.END

    context.user_data["import_contacts"] = contacts
    context.user_data["import_errors"] = errors

    msg = t("tg.found_n", lang=LANG).format(n=len(contacts))
    if errors:
        msg += t("tg.errors_n", lang=LANG).format(n=len(errors))
    msg += t("tg.name_campaign", lang=LANG)

    await update.message.reply_text(msg)
    return IMPORT_NAME


async def import_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.TELEGRAM_ADMIN_IDS:
        return ConversationHandler.END

    campaign_name = update.message.text.strip()
    contacts = context.user_data.get("import_contacts", [])

    if not contacts:
        await update.message.reply_text(t("tg.no_contacts_import", lang=LANG))
        return ConversationHandler.END

    status_msg = await update.message.reply_text(t("tg.importing", lang=LANG))

    contact_ids = []
    for c in contacts:
        cid = await database.upsert_contact(
            first_name=c["first_name"],
            last_name=c["last_name"],
            phone=c["phone"],
            email=c.get("email", ""),
            user_id=BOT_UID,
        )
        contact_ids.append(cid)

    campaign_id = await database.create_campaign(name=campaign_name, user_id=BOT_UID)
    await database.add_contacts_to_campaign(campaign_id, contact_ids)

    await status_msg.edit_text(
        f"{t('tg.import_done', lang=LANG)}\n\n"
        f"{t('tg.campaign', lang=LANG).format(name=_esc(campaign_name))}\n"
        f"{t('tg.contacts', lang=LANG).format(n=len(contacts))}\n\n"
        f"{t('tg.use_send', lang=LANG)}",
        parse_mode="HTML",
    )
    return ConversationHandler.END


# -- Send Conversation -----------------------------------------

@admin_only
async def send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    campaigns = await database.get_all_campaigns()
    if not campaigns:
        await update.message.reply_text(t("tg.no_campaign_use_import", lang=LANG))
        return ConversationHandler.END

    keyboard = []
    for c in campaigns[:10]:
        count = c.get("contact_count", 0)
        keyboard.append([InlineKeyboardButton(
            f"{c['name']} ({count} {t('tg.contacts_suffix', lang=LANG)})",
            callback_data=f"send_{c['id']}",
        )])

    await update.message.reply_text(
        t("tg.choose_campaign", lang=LANG),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SEND_CAMPAIGN


async def send_select_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in config.TELEGRAM_ADMIN_IDS:
        return ConversationHandler.END

    try:
        campaign_id = int(query.data.replace("send_", ""))
    except (ValueError, TypeError):
        await query.edit_message_text(t("tg.campaign_invalid", lang=LANG))
        return ConversationHandler.END

    context.user_data["send_campaign_id"] = campaign_id

    contacts = await database.get_contacts_for_campaign(campaign_id)
    if not contacts:
        await query.edit_message_text(t("tg.no_contacts_in_campaign", lang=LANG))
        return ConversationHandler.END

    context.user_data["send_contacts"] = contacts

    # Offer templates
    templates = await database.get_all_templates()
    # NOTE: tg.recipients_n keeps literal {first_name} braces via {{ }} in the dict.
    text = t("tg.recipients_n", lang=LANG).format(n=len(contacts))
    if templates:
        text += t("tg.templates", lang=LANG)
        for tpl in templates:
            text += f"• /tpl_{tpl['name']}\n"

    text += t("tg.cancel_hint", lang=LANG)
    await query.edit_message_text(text)
    return SEND_MESSAGE


async def send_compose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.TELEGRAM_ADMIN_IDS:
        return ConversationHandler.END

    text = update.message.text.strip()

    # Check if it's a template reference
    if text.startswith("/tpl_"):
        tpl_name = text[5:]
        tpl = await database.get_template(tpl_name)
        if tpl:
            text = tpl["body"]
        else:
            await update.message.reply_text(t("tg.template_not_found", lang=LANG).format(name=tpl_name))
            return SEND_MESSAGE

    context.user_data["send_message"] = text
    contacts = context.user_data.get("send_contacts", [])

    # Preview with safe rendering
    sample = contacts[0] if contacts else {}
    preview = _render_message(text, {
        "first_name": sample.get("first_name", "Contact"),
        "last_name": sample.get("last_name", ""),
        "phone": sample.get("phone", ""),
        "name": f"{sample.get('first_name', '')} {sample.get('last_name', '')}".strip(),
    })

    await update.message.reply_text(
        t("tg.preview", lang=LANG).format(preview=preview[:300], n=len(contacts)),
    )
    return SEND_CONFIRM


async def send_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.TELEGRAM_ADMIN_IDS:
        return ConversationHandler.END

    if update.message.text.strip().lower() not in CONFIRM_WORDS:
        await update.message.reply_text(t("tg.send_cancelled", lang=LANG))
        return ConversationHandler.END

    message = context.user_data.get("send_message", "")
    contacts = context.user_data.get("send_contacts", [])
    campaign_id = context.user_data.get("send_campaign_id")

    if not contacts or not message:
        await update.message.reply_text(t("tg.missing_data", lang=LANG))
        return ConversationHandler.END

    # Save message to campaign for audit trail
    if campaign_id:
        await database.update_campaign_message(campaign_id, message)

    status_msg = await update.message.reply_text(t("tg.sending_n", lang=LANG).format(n=len(contacts)))
    last_edit = [0.0]
    _edit_task = [None]

    def on_progress(current, total, result_status):
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        if now - last_edit[0] >= 3 or current == total:
            last_edit[0] = now
            if _edit_task[0] and not _edit_task[0].done():
                return
            _edit_task[0] = asyncio.get_running_loop().create_task(
                status_msg.edit_text(t("tg.sending_progress", lang=LANG).format(c=current, t=total))
            )

    recipients = [
        {
            "phone": c["phone"],
            "first_name": c.get("first_name", "Contact"),
            "last_name": c.get("last_name", ""),
            "id": c.get("id"),
        }
        for c in contacts
    ]

    results = await wa_client.send_bulk(
        recipients=recipients,
        message_template=message,
        campaign_id=campaign_id,
        on_progress=on_progress,
        user_id=BOT_UID,
    )

    if campaign_id:
        await database.update_campaign_status(campaign_id, "sent")

    await status_msg.edit_text(
        t("tg.send_done", lang=LANG).format(s=results["sent"], f=results["failed"]),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(t("tg.cancelled", lang=LANG))
    return ConversationHandler.END


# -- Callback Query Handler ------------------------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in config.TELEGRAM_ADMIN_IDS:
        await query.edit_message_text(t("tg.access_denied", lang=LANG))
        return

    action = query.data

    if action == "stats":
        contact_count = await database.get_contact_count()
        stats = await database.get_send_stats()
        campaigns = await database.get_all_campaigns()
        text = t("tg.stats_inline", lang=LANG).format(
            c=contact_count,
            n=len(campaigns),
            s=stats.get("sent", 0),
            d=stats.get("delivered", 0),
            f=stats.get("failed", 0),
        )
        await query.edit_message_text(text, parse_mode="HTML")

    elif action == "contacts":
        contacts = await database.get_all_contacts()
        if not contacts:
            await query.edit_message_text(t("tg.no_contacts_short", lang=LANG))
            return
        lines = [f"{t('tg.contacts_title', lang=LANG).format(n=len(contacts))}\n"]
        for c in contacts[:20]:
            lines.append(f"• <code>{_esc(c['phone'])}</code> — {_esc(c.get('first_name', ''))} {_esc(c.get('last_name', ''))}")
        if len(contacts) > 20:
            lines.append(t("tg.and_more", lang=LANG).format(n=len(contacts) - 20))
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")

    elif action == "campaigns":
        campaigns = await database.get_all_campaigns()
        if not campaigns:
            await query.edit_message_text(t("tg.no_campaign_short", lang=LANG))
            return
        lines = [f"{t('tg.campaigns_title', lang=LANG).format(n=len(campaigns))}\n"]
        for c in campaigns[:10]:
            status_emoji = {"draft": "📝", "sent": "✅", "sending": "📤"}.get(c["status"], "⏳")
            lines.append(
                f"{status_emoji} {_esc(c['name'])} — "
                f"{c.get('contact_count', 0)} {t('tg.contacts_suffix', lang=LANG)}"
            )
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")

    elif action == "templates":
        templates = await database.get_all_templates()
        if not templates:
            await query.edit_message_text(t("tg.no_templates", lang=LANG))
            return
        lines = [f"{t('tg.templates_title', lang=LANG).format(n=len(templates))}\n"]
        for tpl in templates:
            lines.append(f"• <b>{_esc(tpl['name'])}</b> [{_esc(tpl['category'])}]")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")

    elif action == "help":
        await query.edit_message_text(
            t("tg.help", lang=LANG),
            parse_mode="HTML",
        )


# -- Build Application -----------------------------------------

def build_telegram_app() -> Application:
    """Build and return the Telegram bot application."""
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Import conversation
    import_conv = ConversationHandler(
        entry_points=[CommandHandler("import", import_start)],
        states={
            IMPORT_CSV: [MessageHandler(filters.Document.ALL, import_csv)],
            IMPORT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Send conversation
    send_conv = ConversationHandler(
        entry_points=[CommandHandler("send", send_start)],
        states={
            SEND_CAMPAIGN: [CallbackQueryHandler(send_select_campaign, pattern=r"^send_\d+$")],
            SEND_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_compose)],
            SEND_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(import_conv)
    app.add_handler(send_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(CommandHandler("templates", cmd_templates))
    app.add_handler(CallbackQueryHandler(button_handler))

    return app
