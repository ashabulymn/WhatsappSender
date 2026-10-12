"""
WhatsApp Bulk Sender — Interactive CLI
Colored terminal interface for importing contacts, composing messages,
and sending WhatsApp messages in bulk.
"""

from __future__ import annotations

import asyncio
import glob
import sys

import database
import csv_handler
from whatsapp import WhatsAppClient, _render_message
import config
from i18n import t

# -- ANSI Colors -----------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"

LANG = config.APP_LANG
# Language-independent yes-words (id + en + legacy fr)
CONFIRM_WORDS = {"ya", "y", "iya", "yes", "ok", "oui"}

wa_client = WhatsAppClient()
# Operating user for CLI single-user mode (resolved to admin after init)
CLI_UID = 1
CLI_DEVICE = ""


async def _resolve_cli_user():
    """Point CLI writes/reads at the admin user and their GoWA device."""
    global CLI_UID, CLI_DEVICE, wa_client
    admin = await database.get_admin_user()
    if admin:
        CLI_UID = admin["id"]
        CLI_DEVICE = admin.get("gowa_device_id") or ""
        wa_client = WhatsAppClient(device_id=CLI_DEVICE)


# -- UI Helpers ------------------------------------------------

def safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def banner():
    print(f"""
{CYAN}{BOLD}
  ╔════════════════════════════════════════════╗
  ║   WhatsApp Bulk Sender — v{config.BOT_VERSION}          ║
  ║     by SoClose Society                     ║
  ╚════════════════════════════════════════════╝
{RESET}""")


def menu():
    print(f"""
{BOLD}{t("cli.menu_title", lang=LANG)}{RESET}
{DIM}─────────────────────────────{RESET}
  {GREEN}1{RESET}  📥  {t("cli.m1", lang=LANG)}
  {GREEN}2{RESET}  👥  {t("cli.m2", lang=LANG)}
  {GREEN}3{RESET}  📢  {t("cli.m3", lang=LANG)}
  {GREEN}4{RESET}  📊  {t("cli.m4", lang=LANG)}
  {GREEN}5{RESET}  📋  {t("cli.m5", lang=LANG)}
  {GREEN}6{RESET}  📨  {t("cli.m6", lang=LANG)}
  {GREEN}7{RESET}  💬  {t("cli.m7", lang=LANG)}
  {GREEN}8{RESET}  🗑   {t("cli.m8", lang=LANG)}
  {GREEN}0{RESET}  🚪  {t("cli.m0", lang=LANG)}
{DIM}─────────────────────────────{RESET}""")


def pick(options: list[str], prompt: str | None = None) -> int | None:
    """Show numbered options, return selected index or None for cancel."""
    prompt = prompt or t("cli.choice", lang=LANG)
    for i, opt in enumerate(options, 1):
        print(f"  {GREEN}{i}{RESET}  {opt}")
    print(f"  {DIM}0{RESET}  {t('cli.back', lang=LANG)}")

    choice = safe_input(f"\n  {BOLD}{prompt}:{RESET} ").strip()
    if not choice or choice == "0":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    print(f"  {RED}{t('cli.invalid_choice', lang=LANG)}{RESET}")
    return None


def progress_bar(current: int, total: int, status: str = ""):
    bar_len = 30
    filled = int(bar_len * current / total) if total > 0 else 0
    bar = f"{'█' * filled}{'░' * (bar_len - filled)}"
    color = GREEN if status == "sent" else RED
    sys.stdout.write(f"\r  {bar} {current}/{total} {color}{status}{RESET}")
    sys.stdout.flush()
    if current == total:
        print()


# -- Commands --------------------------------------------------

async def cmd_import():
    """Import contacts from a CSV file."""
    print(f"\n{BOLD}{t('cli.import_title', lang=LANG)}{RESET}\n")

    # Scan for CSV files in current directory
    csv_files = sorted(glob.glob("*.csv"))
    if not csv_files:
        print(f"  {YELLOW}{t('cli.no_csv', lang=LANG)}{RESET}")
        print(f"  {DIM}{t('cli.place_csv', lang=LANG)}{RESET}\n")
        return

    print(f"  {t('cli.csv_found', lang=LANG)}\n")
    idx = pick(csv_files, t("cli.choose_file", lang=LANG))
    if idx is None:
        return

    csv_path = csv_files[idx]
    print(f"\n  {YELLOW}{t('cli.reading', lang=LANG).format(path=csv_path)}{RESET}")

    with open(csv_path, "rb") as f:
        content = f.read()

    contacts, errors = csv_handler.parse_csv(content, lang=LANG)

    if errors:
        print(f"\n  {YELLOW}{t('cli.warnings', lang=LANG).format(n=len(errors))}{RESET}")
        for err in errors[:5]:
            print(f"    {DIM}{err}{RESET}")
        if len(errors) > 5:
            print(f"    {DIM}{t('cli.and_more', lang=LANG).format(n=len(errors) - 5)}{RESET}")

    if not contacts:
        print(f"\n  {RED}{t('cli.no_valid', lang=LANG)}{RESET}\n")
        return

    print(f"\n  {GREEN}{t('cli.n_valid', lang=LANG).format(n=len(contacts))}{RESET}")
    print(f"\n  {DIM}{t('cli.preview3', lang=LANG)}{RESET}")
    for c in contacts[:3]:
        print(f"    {c['first_name']} {c['last_name']} — {CYAN}{c['phone']}{RESET}")

    # Create a campaign for this import
    campaign_name = safe_input(f"\n  {BOLD}{t('cli.campaign_name', lang=LANG)}{RESET} ").strip()
    if not campaign_name:
        campaign_name = t("cli.import_of", lang=LANG).format(path=csv_path)

    # Save to database
    print(f"\n  {YELLOW}{t('cli.saving', lang=LANG)}{RESET}")
    contact_ids = []
    for c in contacts:
        cid = await database.upsert_contact(
            first_name=c["first_name"],
            last_name=c["last_name"],
            phone=c["phone"],
            email=c.get("email", ""),
            user_id=CLI_UID,
        )
        contact_ids.append(cid)

    # Create campaign and link contacts
    campaign_id = await database.create_campaign(name=campaign_name, message="", user_id=CLI_UID)
    await database.add_contacts_to_campaign(campaign_id, contact_ids)

    print(f"  {GREEN}{t('cli.imported_to', lang=LANG).format(n=len(contacts), name=campaign_name)}{RESET}")
    print(f"  {DIM}{t('cli.campaign_id', lang=LANG).format(id=campaign_id)}{RESET}\n")


async def cmd_contacts():
    """Display all contacts."""
    contacts = await database.get_all_contacts()
    if not contacts:
        print(f"\n  {YELLOW}{t('cli.no_contacts_use1', lang=LANG)}{RESET}\n")
        return

    print(f"\n{BOLD}{t('cli.contacts_title', lang=LANG).format(n=len(contacts))}{RESET}")
    print(f"{DIM}{'─' * 65}{RESET}")
    print(f"  {'#':<5} {t('cli.th_first', lang=LANG):<15} {t('cli.th_last', lang=LANG):<15} {t('cli.th_phone', lang=LANG):<20} {t('cli.th_email', lang=LANG)}")
    print(f"{DIM}{'─' * 65}{RESET}")

    for i, c in enumerate(contacts[:50], 1):
        fn = (c.get("first_name") or "")[:13]
        ln = (c.get("last_name") or "")[:13]
        print(f"  {i:<5} {fn:<15} {ln:<15} {CYAN}{c['phone']:<20}{RESET} {c.get('email', '')}")

    if len(contacts) > 50:
        print(f"\n  {DIM}{t('cli.and_more', lang=LANG).format(n=len(contacts) - 50)}{RESET}")
    print()


async def cmd_send():
    """Send messages to a campaign's contacts."""
    print(f"\n{BOLD}{t('cli.send_title', lang=LANG)}{RESET}\n")

    campaigns = await database.get_all_campaigns()
    if not campaigns:
        print(f"  {YELLOW}{t('cli.no_campaign_import', lang=LANG)}{RESET}\n")
        return

    # Select campaign
    options = [
        f"{c['name']} — {c['contact_count']} {t('cli.contacts_suffix', lang=LANG)} [{c['status']}]"
        for c in campaigns
    ]
    idx = pick(options, t("cli.choose_campaign", lang=LANG))
    if idx is None:
        return

    campaign = campaigns[idx]
    contacts = await database.get_contacts_for_campaign(campaign["id"])

    if not contacts:
        print(f"\n  {YELLOW}{t('cli.no_contacts_in_campaign', lang=LANG)}{RESET}\n")
        return

    print(f"\n  {t('cli.campaign', lang=LANG)} {BOLD}{campaign['name']}{RESET}")
    print(f"  {t('cli.contacts', lang=LANG)} {CYAN}{len(contacts)}{RESET}")

    # Select or compose message
    print(f"\n  {BOLD}{t('cli.message', lang=LANG)}{RESET}")
    # NOTE: cli.placeholders keeps literal {first_name} braces — no .format() call.
    print(f"  {DIM}{t('cli.placeholders', lang=LANG)}{RESET}")

    # Offer templates
    templates = await database.get_all_templates()
    if templates:
        print(f"\n  {BOLD}{t('cli.templates_avail', lang=LANG)}{RESET}")
        tpl_idx = pick(
            [f"{tpl['name']} [{tpl['category']}]" for tpl in templates] + [t("cli.write_custom", lang=LANG)],
            t("cli.template", lang=LANG)
        )
        if tpl_idx is None:
            return

        if tpl_idx < len(templates):
            message = templates[tpl_idx]["body"]
            print(f"\n  {DIM}{t('cli.template_loaded', lang=LANG)}{RESET}")
        else:
            message = safe_input(f"\n  {t('cli.message_prompt', lang=LANG)}").strip()
    else:
        message = safe_input(f"\n  {t('cli.message_prompt', lang=LANG)}").strip()

    if not message:
        print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")
        return

    # Preview
    sample = contacts[0]
    preview = _render_message(message, {
        "first_name": sample.get("first_name", "Contact"),
        "last_name": sample.get("last_name", ""),
        "phone": sample.get("phone", ""),
        "name": f"{sample.get('first_name', '')} {sample.get('last_name', '')}".strip(),
    })

    print(f"\n  {BOLD}{t('cli.preview', lang=LANG)}{RESET}")
    print(f"  {DIM}{'─' * 40}{RESET}")
    print(f"  {preview[:300]}")
    print(f"  {DIM}{'─' * 40}{RESET}")
    print(f"\n  {t('cli.recipients', lang=LANG).format(n=len(contacts))}")
    print(f"  {t('cli.provider', lang=LANG).format(p=config.WA_PROVIDER.upper())}")

    # Dry run option
    dry = safe_input(f"\n  {t('cli.dry_run_prompt', lang=LANG)}").strip().lower()
    dry_run = dry in CONFIRM_WORDS

    if not dry_run:
        confirm = safe_input(f"  {t('cli.confirm_send', lang=LANG).format(n=len(contacts))}").strip().lower()
        if confirm not in CONFIRM_WORDS:
            print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")
            return

    # Save message and update campaign
    campaign_id = campaign["id"]
    await database.update_campaign_message(campaign_id, message)
    await database.update_campaign_status(campaign_id, "sending")

    if dry_run:
        print(f"\n  {YELLOW}{t('cli.dry_run_banner', lang=LANG)}{RESET}")
        print(f"  {t('cli.would_send', lang=LANG).format(n=len(contacts))}\n")
        for c in contacts[:2]:
            body = _render_message(message, {
                "first_name": c.get("first_name", "Contact"),
                "last_name": c.get("last_name", ""),
                "phone": c.get("phone", ""),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
            })
            print(f"  → {c['phone']}: {body[:100]}")
        await database.update_campaign_status(campaign_id, "draft")
        print()
        return

    # Send
    print(f"\n  {YELLOW}{t('cli.sending', lang=LANG)}{RESET}\n")

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
        on_progress=lambda cur, tot, st: progress_bar(cur, tot, st),
        user_id=CLI_UID,
    )

    await database.update_campaign_status(campaign_id, "sent")
    print(f"\n  {GREEN}{t('cli.done', lang=LANG)}{RESET}")
    print(f"  {t('cli.sent_failed', lang=LANG).format(s=results['sent'], f=results['failed'])}\n")


async def cmd_stats():
    """Show sending statistics."""
    stats = await database.get_send_stats()
    contact_count = await database.get_contact_count()
    campaigns = await database.get_all_campaigns()

    print(f"""
{BOLD}{t('cli.stats_title', lang=LANG)}{RESET}
{DIM}─────────────────────────────{RESET}
  {t('cli.contacts_l', lang=LANG)}     {CYAN}{contact_count}{RESET}
  {t('cli.campaigns_l', lang=LANG)}    {CYAN}{len(campaigns)}{RESET}
  {t('cli.total_sends', lang=LANG)} {CYAN}{stats.get('total', 0)}{RESET}

  {BOLD}{t('cli.statuses', lang=LANG)}{RESET}
  {t('cli.pending', lang=LANG)}   {YELLOW}{stats.get('queued', 0)}{RESET}
  {t('cli.sent', lang=LANG)}      {GREEN}{stats.get('sent', 0)}{RESET}
  {t('cli.delivered', lang=LANG)}       {GREEN}{stats.get('delivered', 0)}{RESET}
  {t('cli.read', lang=LANG)}          {BLUE}{stats.get('read', 0)}{RESET}
  {t('cli.failed', lang=LANG)}      {RED}{stats.get('failed', 0)}{RESET}
""")


async def cmd_templates():
    """Manage message templates."""
    templates = await database.get_all_templates()

    print(f"\n{BOLD}{t('cli.templates_title', lang=LANG).format(n=len(templates))}{RESET}")
    print(f"{DIM}{'─' * 50}{RESET}")

    for tpl in templates:
        print(f"  {MAGENTA}{tpl['name']}{RESET} [{tpl['category']}]")
        body_preview = tpl["body"].replace("\n", " ")[:70]
        ellipsis = "..." if len(tpl["body"]) > 70 else ""
        print(f"  {DIM}{body_preview}{ellipsis}{RESET}\n")

    print(f"  {BOLD}{t('cli.actions', lang=LANG)}{RESET}")
    action = pick(
        [t("cli.act_create", lang=LANG), t("cli.act_delete", lang=LANG), t("cli.act_back", lang=LANG)],
        t("cli.action", lang=LANG),
    )

    if action == 0:
        name = safe_input(f"\n  {t('cli.name', lang=LANG)}").strip()
        category = safe_input(f"  {t('cli.category', lang=LANG)}").strip() or "general"
        print(f"  {t('cli.body_lines', lang=LANG)}")
        lines = []
        while True:
            line = safe_input("  > ")
            if line == "":
                break
            lines.append(line)
        body = "\n".join(lines)
        if name and body:
            await database.create_template(name, category, body, user_id=CLI_UID)
            print(f"\n  {GREEN}{t('cli.template_created', lang=LANG).format(name=name)}{RESET}\n")
        else:
            print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")

    elif action == 1:
        if not templates:
            print(f"  {YELLOW}{t('cli.no_templates', lang=LANG)}{RESET}\n")
            return
        idx = pick([tpl["name"] for tpl in templates], t("cli.delete_which", lang=LANG))
        if idx is not None:
            await database.delete_template(templates[idx]["name"])
            print(f"  {GREEN}{t('cli.template_deleted', lang=LANG)}{RESET}\n")


async def cmd_history():
    """Show campaign history."""
    campaigns = await database.get_all_campaigns()
    if not campaigns:
        print(f"\n  {YELLOW}{t('cli.no_campaigns', lang=LANG)}{RESET}\n")
        return

    print(f"\n{BOLD}{t('cli.history_title', lang=LANG).format(n=len(campaigns))}{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")

    for c in campaigns[:15]:
        status_color = GREEN if c["status"] == "sent" else YELLOW
        status_emoji = {"draft": "📝", "sent": "✅", "sending": "📤"}.get(c["status"], "⏳")
        count = c.get("contact_count", 0)
        print(f"  {status_emoji} {BOLD}{c['name']}{RESET} — {status_color}{c['status']}{RESET} — {count} contacts — {c['created_at'][:16]}")

        if c["status"] == "sent":
            stats = await database.get_campaign_stats(c["id"])
            sent = stats.get("sent", 0) + stats.get("delivered", 0) + stats.get("read", 0)
            failed = stats.get("failed", 0)
            print(f"    {DIM}{t('cli.results', lang=LANG).format(s=sent, f=failed)}{RESET}")
        print()


async def cmd_test():
    """Test sending a single message."""
    print(f"\n{BOLD}{t('cli.test_title', lang=LANG)}{RESET}")
    phone = safe_input(f"  {t('cli.number_ex', lang=LANG)}").strip()
    if not phone:
        print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")
        return

    message = safe_input(f"  {t('cli.message_prompt', lang=LANG)}").strip()
    if not message:
        print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")
        return

    print(f"  {YELLOW}{t('cli.sending2', lang=LANG)}{RESET}")
    result = await wa_client.send_message(phone, message)

    if result["status"] == "sent":
        print(f"  {GREEN}{t('cli.msg_sent', lang=LANG).format(sid=result.get('sid', 'N/A'))}{RESET}\n")
    else:
        print(f"  {RED}{t('cli.send_failed', lang=LANG).format(err=result.get('error', 'Unknown'))}{RESET}\n")


async def cmd_delete_contacts():
    """Delete all contacts."""
    count = await database.get_contact_count()
    if count == 0:
        print(f"\n  {YELLOW}{t('cli.no_contacts_del', lang=LANG)}{RESET}\n")
        return

    confirm = safe_input(f"\n  {t('cli.delete_confirm', lang=LANG).format(n=count)}").strip().lower()
    if confirm in CONFIRM_WORDS:
        deleted = await database.delete_all_contacts()
        print(f"  {GREEN}{t('cli.deleted_n', lang=LANG).format(n=deleted)}{RESET}\n")
    else:
        print(f"  {RED}{t('cli.cancelled', lang=LANG)}{RESET}\n")


# -- Main Loop -------------------------------------------------

async def run_cli():
    """Main CLI loop."""
    await database.init()
    await _resolve_cli_user()
    banner()

    while True:
        menu()
        choice = safe_input(f"  {BOLD}{t('cli.choice', lang=LANG)}:{RESET} ").strip()

        if choice == "1":
            await cmd_import()
        elif choice == "2":
            await cmd_contacts()
        elif choice == "3":
            await cmd_send()
        elif choice == "4":
            await cmd_stats()
        elif choice == "5":
            await cmd_templates()
        elif choice == "6":
            await cmd_history()
        elif choice == "7":
            await cmd_test()
        elif choice == "8":
            await cmd_delete_contacts()
        elif choice == "0":
            print(f"\n  {CYAN}{t('cli.goodbye', lang=LANG)}{RESET}\n")
            break
        else:
            print(f"\n  {RED}{t('cli.invalid_option', lang=LANG)}{RESET}")
