"""
WhatsApp Bulk Sender — Internationalization (i18n)
Bilingual UI strings: Indonesian (id) and English (en).

Usage:
    from i18n import t
    t("dash.nav")                  # uses APP_LANG env (default "id")
    t("dash.nav", lang="en")       # explicit language

Language is selected via the APP_LANG environment variable ("id" | "en")
and can be overridden per-session (the Streamlit dashboard offers a toggle
in the sidebar stored in st.session_state["lang"]).
"""

from __future__ import annotations

import os

APP_LANG = os.getenv("APP_LANG", "id").lower()
if APP_LANG not in ("id", "en"):
    APP_LANG = "id"

STRINGS: dict[str, dict[str, str]] = {
    # =========================================================
    # Indonesian
    # =========================================================
    "id": {
        # -- Dashboard: auth gate ----------------------------------
        "dash.auth_title": "🔒 WhatsApp Bulk Sender",
        "dash.auth_prompt": "Masukkan kata sandi untuk mengakses dashboard.",
        "dash.auth_password": "Kata sandi",
        "dash.auth_login": "Masuk",
        "dash.auth_wrong": "Kata sandi salah.",
        # -- Dashboard: sidebar ------------------------------------
        "dash.sidebar_title": "📱 WhatsApp Sender",
        "dash.nav": "Navigasi",
        "dash.lang_label": "🌐 Bahasa",
        "dash.page_dashboard": "🏠 Dashboard",
        "dash.page_import": "📥 Impor CSV",
        "dash.page_send": "📢 Kirim",
        "dash.page_contacts": "👥 Kontak",
        "dash.page_stats": "📊 Statistik",
        "dash.page_templates": "📋 Templat",
        "dash.page_test": "💬 Tes Pesan",
        "dash.page_config": "⚙️ Konfigurasi",
        # -- Dashboard page ----------------------------------------
        "dash.metric_contacts": "Kontak",
        "dash.metric_campaigns": "Kampanye",
        "dash.metric_sent": "Pesan terkirim",
        "dash.metric_failed": "Gagal",
        "dash.metric_delivery_rate": "Tingkat pengiriman",
        "dash.metric_delivered": "Terkirim",
        "dash.metric_read": "Dibaca",
        "dash.metric_total": "Total",
        "dash.recent_campaigns": "Kampanye terbaru",
        "dash.no_campaigns": "Belum ada kampanye.",
        "dash.contacts_suffix": "kontak",
        # -- Import page -------------------------------------------
        "dash.import_title": "📥 Impor kontak",
        "dash.choose_csv": "Pilih file CSV",
        "dash.campaign_name": "Nama kampanye",
        "dash.campaign_name_ph": "Kampanye saya",
        "dash.import_btn": "📥 Impor",
        "dash.warnings_n": "⚠ {n} peringatan",
        "dash.importing_n": "Mengimpor {n} kontak...",
        "dash.imported_n": "{n} kontak diimpor ke '{name}'",
        "dash.no_valid_contacts": "Tidak ada kontak valid.",
        "dash.csv_format_title": "Format CSV yang diharapkan",
        # -- Send page ---------------------------------------------
        "dash.send_title": "📢 Kirim pesan",
        "dash.no_campaign_import_first": "Belum ada kampanye. Impor kontak terlebih dahulu.",
        "dash.campaign_label": "Kampanye",
        "dash.contacts_in_this_campaign": "**{n}** kontak dalam kampanye ini.",
        "dash.template_label": "Templat",
        "dash.write_message": "(Tulis pesan)",
        "dash.message_label": "Pesan",
        "dash.placeholders_help": "Placeholder: {first_name}, {last_name}, {phone}, {name}",
        "dash.preview_label": "**Pratinjau:**",
        "dash.dry_run": "Mode uji (dry run)",
        "dash.send_btn": "📤 Kirim",
        "dash.dry_run_msg": "**DRY RUN** — Tidak ada pesan terkirim. {n} penerima.",
        "dash.sending_progress": "Mengirim: {i}/{total} — Terkirim: {s} | Gagal: {f}",
        "dash.done_sent_failed": "Selesai! Terkirim: {s} | Gagal: {f}",
        # -- Contacts page -----------------------------------------
        "dash.contacts_title": "👥 Kontak",
        "dash.no_contacts_import_csv": "Belum ada kontak. Impor CSV untuk memulai.",
        "dash.total_contacts": "Total kontak",
        # -- Statistics page ---------------------------------------
        "dash.stats_title": "📊 Statistik",
        "dash.messages_by_status": "Pesan per status",
        "dash.status_sent": "Terkirim",
        "dash.status_delivered": "Terkirim",
        "dash.status_read": "Dibaca",
        "dash.status_failed": "Gagal",
        "dash.status_pending": "Menunggu",
        "dash.col_status": "Status",
        "dash.col_count": "Jumlah",
        "dash.campaigns_label": "Kampanye",
        "dash.rate_n": "Tingkat: {rate}%",
        "dash.recent_messages": "Pesan terbaru",
        "dash.no_messages": "Belum ada pesan.",
        # -- Templates page ----------------------------------------
        "dash.templates_title": "📋 Templat pesan",
        "dash.create_template": "➕ Buat templat",
        "dash.template_name": "Nama templat",
        "dash.category": "Kategori",
        "dash.content": "Isi",
        "dash.create_btn": "Buat",
        "dash.template_created": "Templat '{name}' dibuat!",
        "dash.delete_label": "Hapus",
        "dash.template_deleted": "Templat '{name}' dihapus.",
        # -- Test page ---------------------------------------------
        "dash.test_title": "💬 Tes kirim WhatsApp",
        "dash.phone_intl": "Nomor (format internasional)",
        "dash.phone_ph": "+6281234567890",
        "dash.test_msg_ph": "Halo! Tes dari WhatsApp Bulk Sender.",
        "dash.send_btn2": "Kirim",
        "dash.sending": "Mengirim...",
        "dash.msg_sent_sid": "Pesan terkirim! SID: {sid}",
        "dash.failed_err": "Gagal: {err}",
        # -- Configuration page ------------------------------------
        "dash.config_title": "⚙️ Konfigurasi",
        "dash.config_ok": "Konfigurasi OK!",
        "dash.cfg_bot": "Bot",
        "dash.cfg_version": "Versi",
        "dash.cfg_provider": "Penyedia",
        "dash.cfg_webhook_port": "Port Webhook",
        "dash.cfg_rate_limit": "Batas laju",
        "dash.cfg_database": "Basis data",

        # =========================================================
        # CLI
        # =========================================================
        "cli.menu_title": "Menu Utama",
        "cli.m1": "Impor kontak (CSV)",
        "cli.m2": "Lihat kontak",
        "cli.m3": "Kirim pesan",
        "cli.m4": "Statistik",
        "cli.m5": "Templat",
        "cli.m6": "Riwayat pengiriman",
        "cli.m7": "Tes pesan",
        "cli.m8": "Hapus semua kontak",
        "cli.m0": "Keluar",
        "cli.back": "Kembali",
        "cli.choice": "Pilihan",
        "cli.invalid_choice": "Pilihan tidak valid.",
        "cli.import_title": "📥 Impor kontak",
        "cli.no_csv": "Tidak ada file CSV di direktori saat ini.",
        "cli.place_csv": "Letakkan file .csv di sini lalu jalankan impor lagi.",
        "cli.csv_found": "File CSV ditemukan:",
        "cli.choose_file": "Pilih file",
        "cli.reading": "Membaca {path}...",
        "cli.warnings": "⚠ {n} peringatan:",
        "cli.and_more": "... dan {n} lagi",
        "cli.no_valid": "Tidak ada kontak valid.",
        "cli.n_valid": "✅ {n} kontak valid ditemukan",
        "cli.preview3": "Pratinjau (3 pertama):",
        "cli.campaign_name": "Nama kampanye:",
        "cli.import_of": "Impor {path}",
        "cli.saving": "Merekam...",
        "cli.imported_to": "✅ {n} kontak diimpor ke kampanye '{name}'",
        "cli.campaign_id": "ID kampanye: {id}",
        "cli.contacts_title": "👥 Kontak ({n})",
        "cli.no_contacts_use1": "Belum ada kontak. Gunakan opsi 1 untuk mengimpor CSV.",
        "cli.th_first": "Nama depan",
        "cli.th_last": "Nama belakang",
        "cli.th_phone": "Telepon",
        "cli.th_email": "Email",
        "cli.send_title": "📢 Kirim pesan",
        "cli.no_campaign_import": "Belum ada kampanye. Impor kontak terlebih dahulu.",
        "cli.choose_campaign": "Pilih kampanye",
        "cli.no_contacts_in_campaign": "Tidak ada kontak dalam kampanye ini.",
        "cli.campaign": "Kampanye:",
        "cli.contacts": "Kontak:",
        "cli.message": "Pesan:",
        "cli.placeholders": "Placeholder: {first_name}, {last_name}, {phone}, {name}",
        "cli.templates_avail": "Templat tersedia:",
        "cli.write_custom": "Tulis pesan sendiri",
        "cli.template": "Templat",
        "cli.template_loaded": "Templat dimuat.",
        "cli.message_prompt": "Pesan: ",
        "cli.cancelled": "Dibatalkan.",
        "cli.preview": "Pratinjau:",
        "cli.recipients": "Penerima: {n}",
        "cli.provider": "Penyedia: {p}",
        "cli.dry_run_prompt": "Mode uji (dry run)? (ya/tidak) [tidak]: ",
        "cli.confirm_send": "Konfirmasi kirim ke {n} kontak? (ya/tidak): ",
        "cli.dry_run_banner": "🧪 DRY RUN — Tidak ada pesan terkirim",
        "cli.would_send": "{n} pesan akan dikirim.",
        "cli.sending": "📤 Mengirim...",
        "cli.done": "✅ Selesai!",
        "cli.sent_failed": "Terkirim: {s} | Gagal: {f}",
        "cli.stats_title": "📊 Statistik",
        "cli.contacts_l": "👥 Kontak:",
        "cli.campaigns_l": "📢 Kampanye:",
        "cli.total_sends": "📨 Total kirim:",
        "cli.statuses": "Status",
        "cli.pending": "⏳ Menunggu:",
        "cli.sent": "✅ Terkirim:",
        "cli.delivered": "📬 Diterima:",
        "cli.read": "👁 Dibaca:",
        "cli.failed": "❌ Gagal:",
        "cli.templates_title": "📋 Templat ({n})",
        "cli.actions": "Tindakan:",
        "cli.act_create": "Buat templat",
        "cli.act_delete": "Hapus templat",
        "cli.act_back": "Kembali",
        "cli.action": "Tindakan",
        "cli.name": "Nama: ",
        "cli.category": "Kategori (general/broadcast/info): ",
        "cli.body_lines": "Isi pesan (baris kosong untuk selesai):",
        "cli.template_created": "✅ Templat '{name}' dibuat!",
        "cli.no_templates": "Belum ada templat.",
        "cli.delete_which": "Hapus",
        "cli.template_deleted": "✅ Templat dihapus.",
        "cli.history_title": "📨 Riwayat kampanye ({n})",
        "cli.no_campaigns": "Belum ada kampanye.",
        "cli.results": "Hasil: {s} terkirim, {f} gagal",
        "cli.test_title": "💬 Tes pesan WhatsApp",
        "cli.number_ex": "Nomor (cth: +6281234567890): ",
        "cli.sending2": "Mengirim...",
        "cli.msg_sent": "✅ Pesan terkirim! SID: {sid}",
        "cli.send_failed": "❌ Gagal: {err}",
        "cli.delete_confirm": "Hapus {n} kontak? (ya/tidak): ",
        "cli.no_contacts_del": "Tidak ada kontak untuk dihapus.",
        "cli.deleted_n": "✅ {n} kontak dihapus.",
        "cli.goodbye": "Sampai jumpa! 👋",
        "cli.invalid_option": "Opsi tidak valid.",
        "cli.contacts_suffix": "kontak",
        # =========================================================
        # CSV handler
        # =========================================================
        "csv.decode_error": "Tidak bisa mendekode file CSV.",
        "csv.parse_error": "Galat parsing CSV: {e}",
        "csv.empty": "File CSV kosong.",
        "csv.no_phone_col": "Kolom 'phone' tidak ditemukan. Kolom tersedia: {cols}",
        "csv.invalid_number": "Baris {n}: nomor tidak valid '{phone}'",
        # =========================================================
        # main.py (console)
        # =========================================================
        "main.packages_missing": "❌ Paket kurang: {pkgs}",
        "main.install_hint": "   Instal dengan: pip install -r requirements.txt",
        "main.telegram_token_required": "TELEGRAM_BOT_TOKEN diperlukan untuk mode Telegram",
        "main.epilog": (
            "\nMode:\n"
            "  (default)     CLI interaktif — impor CSV, kirim massal\n"
            "  --telegram    Bot Telegram + server webhook\n"
            "  --webhook     Server webhook saja (callback pengiriman)\n"
            "  --dashboard   Dashboard web Streamlit\n"
            "\nContoh:\n"
            "  python main.py                    # CLI\n"
            "  python main.py --telegram         # Produksi\n"
            "  python main.py --dashboard        # Web UI\n"
            "  python main.py --webhook          # API saja\n        "
        ),
        "main.help_telegram": "Jalankan bot Telegram + webhook",
        "main.help_webhook": "Jalankan server webhook saja",
        "main.help_dashboard": "Jalankan dashboard Streamlit",
        # =========================================================
        # Peringatan konfigurasi
        # =========================================================
        "cfgw.twilio_sid": "TWILIO_ACCOUNT_SID belum diisi",
        "cfgw.twilio_token": "TWILIO_AUTH_TOKEN belum diisi",
        "cfgw.twilio_from": "TWILIO_WHATSAPP_FROM belum diisi",
        "cfgw.meta_phone_id": "WA_PHONE_NUMBER_ID belum diisi",
        "cfgw.meta_token": "WA_ACCESS_TOKEN belum diisi",
        "cfgw.gowa_url": "GOWA_BASE_URL belum diisi",
        "cfgw.gowa_auth": "GOWA_USERNAME / GOWA_PASSWORD belum diisi (Basic Auth GoWA)",
        "cfgw.unknown_provider": "WA_PROVIDER tidak dikenal: {p} (gunakan 'twilio', 'meta', atau 'gowa')",
        "cfgw.rate": "WA_MESSAGES_PER_SECOND = {v}, harus > 0",
        # =========================================================
        # Telegram bot
        # =========================================================
        "tg.access_denied": "⛔ Akses ditolak. Admin saja.",
        "tg.start_admin": "Panel administrasi.",
        "tg.provider": "Penyedia: {p}",
        "tg.btn_stats": "📊 Statistik",
        "tg.btn_contacts": "👥 Kontak",
        "tg.btn_campaigns": "📢 Kampanye",
        "tg.btn_templates": "📋 Templat",
        "tg.btn_help": "ℹ️ Bantuan",
        "tg.stats_title": "📊 <b>Statistik</b>",
        "tg.contacts_l": "👥 Kontak: {n}",
        "tg.campaigns_l": "📢 Kampanye: {n}",
        "tg.total_sends": "📨 Total kirim: {n}",
        "tg.statuses": "<b>Status</b>",
        "tg.pending": "⏳ Menunggu: {n}",
        "tg.sent": "✅ Terkirim: {n}",
        "tg.delivered": "📬 Diterima: {n}",
        "tg.read": "👁 Dibaca: {n}",
        "tg.failed": "❌ Gagal: {n}",
        "tg.no_contacts_use_import": "Belum ada kontak. Gunakan /import untuk mengimpor CSV.",
        "tg.contacts_title": "👥 <b>Kontak</b> ({n})",
        "tg.and_more": "... dan {n} lagi",
        "tg.no_templates": "Belum ada templat.",
        "tg.templates_title": "📋 <b>Templat</b> ({n})",
        "tg.import_title": "📥 <b>Impor CSV</b>",
        "tg.import_instructions": (
            "Kirimkan file CSV dengan kolom:\n"
            "<code>phone</code>, <code>first_name</code>, <code>last_name</code> (opsional), <code>email</code> (opsional)\n\n"
            "Ketik /cancel untuk membatalkan."
        ),
        "tg.send_csv": "Kirim file .csv",
        "tg.import_failed": "❌ Impor gagal:\n{err}",
        "tg.no_valid": "Tidak ada kontak valid.",
        "tg.found_n": "✅ {n} kontak ditemukan.",
        "tg.errors_n": "\n⚠ {n} galat.",
        "tg.name_campaign": "\n\nBeri nama kampanye ini:",
        "tg.no_contacts_import": "❌ Tidak ada kontak untuk diimpor.",
        "tg.importing": "⏳ Mengimpor...",
        "tg.import_done": "✅ <b>Impor selesai</b>",
        "tg.campaign": "Kampanye: {name}",
        "tg.contacts": "Kontak: {n}",
        "tg.use_send": "Gunakan /send untuk mengirim pesan.",
        "tg.no_campaign_use_import": "Belum ada kampanye. Gunakan /import dulu.",
        "tg.choose_campaign": "📢 <b>Pilih kampanye:</b>",
        "tg.campaign_invalid": "Kampanye tidak valid.",
        "tg.no_contacts_in_campaign": "Tidak ada kontak dalam kampanye ini.",
        "tg.recipients_n": (
            "📝 {n} penerima.\n\n"
            "Tulis pesanmu atau pilih templat.\n"
            "Placeholder: {{first_name}}, {{last_name}}, {{phone}}\n\n"
        ),
        "tg.templates": "Templat:\n",
        "tg.cancel_hint": "\nKetik /cancel untuk membatalkan.",
        "tg.template_not_found": "Templat '{name}' tidak ditemukan.",
        "tg.preview": "📋 Pratinjau:\n\n{preview}\n\nPenerima: {n}\n\nKonfirmasi? Ketik ya atau /cancel",
        "tg.send_cancelled": "❌ Pengiriman dibatalkan.",
        "tg.missing_data": "❌ Data tidak lengkap.",
        "tg.sending_n": "📤 Mengirim... 0/{n}",
        "tg.sending_progress": "📤 Mengirim... {c}/{t}",
        "tg.send_done": "✅ <b>Pengiriman selesai</b>\n\nTerkirim: {s}\nGagal: {f}",
        "tg.cancelled": "❌ Dibatalkan.",
        "tg.stats_inline": (
            "📊 <b>Statistik</b>\n\n"
            "👥 Kontak: {c}\n📢 Kampanye: {n}\n\n"
            "✅ Terkirim: {s} | 📬 Diterima: {d} | ❌ Gagal: {f}"
        ),
        "tg.no_contacts_short": "Belum ada kontak. Gunakan /import.",
        "tg.no_campaign_short": "Belum ada kampanye. Gunakan /import.",
        "tg.campaigns_title": "📢 <b>Kampanye</b> ({n})",
        "tg.contacts_suffix": "kontak",
        "tg.help": (
            "ℹ️ <b>Perintah</b>\n\n"
            "/start — Menu utama\n"
            "/stats — Statistik\n"
            "/contacts — Daftar kontak\n"
            "/import — Impor CSV\n"
            "/send — Kirim kampanye\n"
            "/templates — Templat\n"
        ),
    },
    # =========================================================
    # English
    # =========================================================
    "en": {
        # -- Dashboard: auth gate ----------------------------------
        "dash.auth_title": "🔒 WhatsApp Bulk Sender",
        "dash.auth_prompt": "Enter the password to access the dashboard.",
        "dash.auth_password": "Password",
        "dash.auth_login": "Log in",
        "dash.auth_wrong": "Incorrect password.",
        # -- Dashboard: sidebar ------------------------------------
        "dash.sidebar_title": "📱 WhatsApp Sender",
        "dash.nav": "Navigation",
        "dash.lang_label": "🌐 Language",
        "dash.page_dashboard": "🏠 Dashboard",
        "dash.page_import": "📥 Import CSV",
        "dash.page_send": "📢 Send",
        "dash.page_contacts": "👥 Contacts",
        "dash.page_stats": "📊 Statistics",
        "dash.page_templates": "📋 Templates",
        "dash.page_test": "💬 Test Message",
        "dash.page_config": "⚙️ Configuration",
        # -- Dashboard page ----------------------------------------
        "dash.metric_contacts": "Contacts",
        "dash.metric_campaigns": "Campaigns",
        "dash.metric_sent": "Messages sent",
        "dash.metric_failed": "Failed",
        "dash.metric_delivery_rate": "Delivery rate",
        "dash.metric_delivered": "Delivered",
        "dash.metric_read": "Read",
        "dash.metric_total": "Total",
        "dash.recent_campaigns": "Recent campaigns",
        "dash.no_campaigns": "No campaigns.",
        "dash.contacts_suffix": "contacts",
        # -- Import page -------------------------------------------
        "dash.import_title": "📥 Import contacts",
        "dash.choose_csv": "Choose a CSV file",
        "dash.campaign_name": "Campaign name",
        "dash.campaign_name_ph": "My campaign",
        "dash.import_btn": "📥 Import",
        "dash.warnings_n": "⚠ {n} warnings",
        "dash.importing_n": "Importing {n} contacts...",
        "dash.imported_n": "{n} contacts imported into '{name}'",
        "dash.no_valid_contacts": "No valid contacts found.",
        "dash.csv_format_title": "Expected CSV format",
        # -- Send page ---------------------------------------------
        "dash.send_title": "📢 Send messages",
        "dash.no_campaign_import_first": "No campaigns. Import contacts first.",
        "dash.campaign_label": "Campaign",
        "dash.contacts_in_this_campaign": "**{n}** contacts in this campaign.",
        "dash.template_label": "Template",
        "dash.write_message": "(Write a message)",
        "dash.message_label": "Message",
        "dash.placeholders_help": "Placeholders: {first_name}, {last_name}, {phone}, {name}",
        "dash.preview_label": "**Preview:**",
        "dash.dry_run": "Test mode (dry run)",
        "dash.send_btn": "📤 Send",
        "dash.dry_run_msg": "**DRY RUN** — No messages sent. {n} recipients.",
        "dash.sending_progress": "Sending: {i}/{total} — Sent: {s} | Failed: {f}",
        "dash.done_sent_failed": "Done! Sent: {s} | Failed: {f}",
        # -- Contacts page -----------------------------------------
        "dash.contacts_title": "👥 Contacts",
        "dash.no_contacts_import_csv": "No contacts. Import a CSV to get started.",
        "dash.total_contacts": "Total contacts",
        # -- Statistics page ---------------------------------------
        "dash.stats_title": "📊 Statistics",
        "dash.messages_by_status": "Messages by status",
        "dash.status_sent": "Sent",
        "dash.status_delivered": "Delivered",
        "dash.status_read": "Read",
        "dash.status_failed": "Failed",
        "dash.status_pending": "Pending",
        "dash.col_status": "Status",
        "dash.col_count": "Count",
        "dash.campaigns_label": "Campaigns",
        "dash.rate_n": "Rate: {rate}%",
        "dash.recent_messages": "Recent messages",
        "dash.no_messages": "No messages.",
        # -- Templates page ----------------------------------------
        "dash.templates_title": "📋 Message templates",
        "dash.create_template": "➕ Create a template",
        "dash.template_name": "Template name",
        "dash.category": "Category",
        "dash.content": "Content",
        "dash.create_btn": "Create",
        "dash.template_created": "Template '{name}' created!",
        "dash.delete_label": "Delete",
        "dash.template_deleted": "Template '{name}' deleted.",
        # -- Test page ---------------------------------------------
        "dash.test_title": "💬 Test WhatsApp sending",
        "dash.phone_intl": "Number (international format)",
        "dash.phone_ph": "+14155238886",
        "dash.test_msg_ph": "Hello! Test from WhatsApp Bulk Sender.",
        "dash.send_btn2": "Send",
        "dash.sending": "Sending...",
        "dash.msg_sent_sid": "Message sent! SID: {sid}",
        "dash.failed_err": "Failed: {err}",
        # -- Configuration page ------------------------------------
        "dash.config_title": "⚙️ Configuration",
        "dash.config_ok": "Configuration OK!",
        "dash.cfg_bot": "Bot",
        "dash.cfg_version": "Version",
        "dash.cfg_provider": "Provider",
        "dash.cfg_webhook_port": "Webhook Port",
        "dash.cfg_rate_limit": "Rate Limit",
        "dash.cfg_database": "Database",
        # =========================================================
        # CLI
        # =========================================================
        "cli.menu_title": "Main Menu",
        "cli.m1": "Import contacts (CSV)",
        "cli.m2": "View contacts",
        "cli.m3": "Send messages",
        "cli.m4": "Statistics",
        "cli.m5": "Templates",
        "cli.m6": "Send history",
        "cli.m7": "Test a message",
        "cli.m8": "Delete all contacts",
        "cli.m0": "Quit",
        "cli.back": "Back",
        "cli.choice": "Choice",
        "cli.invalid_choice": "Invalid choice.",
        "cli.import_title": "📥 Import contacts",
        "cli.no_csv": "No CSV file found in the current directory.",
        "cli.place_csv": "Place a .csv file here and rerun the import.",
        "cli.csv_found": "CSV files found:",
        "cli.choose_file": "Choose a file",
        "cli.reading": "Reading {path}...",
        "cli.warnings": "⚠ {n} warning(s):",
        "cli.and_more": "... and {n} more",
        "cli.no_valid": "No valid contacts found.",
        "cli.n_valid": "✅ {n} valid contacts found",
        "cli.preview3": "Preview (first 3):",
        "cli.campaign_name": "Campaign name:",
        "cli.import_of": "Import {path}",
        "cli.saving": "Saving...",
        "cli.imported_to": "✅ {n} contacts imported into campaign '{name}'",
        "cli.campaign_id": "Campaign ID: {id}",
        "cli.contacts_title": "👥 Contacts ({n})",
        "cli.no_contacts_use1": "No contacts. Use option 1 to import a CSV.",
        "cli.th_first": "First name",
        "cli.th_last": "Last name",
        "cli.th_phone": "Phone",
        "cli.th_email": "Email",
        "cli.send_title": "📢 Send messages",
        "cli.no_campaign_import": "No campaigns. Import contacts first.",
        "cli.choose_campaign": "Choose a campaign",
        "cli.no_contacts_in_campaign": "No contacts in this campaign.",
        "cli.campaign": "Campaign:",
        "cli.contacts": "Contacts:",
        "cli.message": "Message:",
        "cli.placeholders": "Placeholders: {first_name}, {last_name}, {phone}, {name}",
        "cli.templates_avail": "Available templates:",
        "cli.write_custom": "Write a custom message",
        "cli.template": "Template",
        "cli.template_loaded": "Template loaded.",
        "cli.message_prompt": "Message: ",
        "cli.cancelled": "Cancelled.",
        "cli.preview": "Preview:",
        "cli.recipients": "Recipients: {n}",
        "cli.provider": "Provider: {p}",
        "cli.dry_run_prompt": "Dry run mode? (yes/no) [no]: ",
        "cli.confirm_send": "Confirm sending to {n} contacts? (yes/no): ",
        "cli.dry_run_banner": "🧪 DRY RUN — No messages sent",
        "cli.would_send": "{n} messages would have been sent.",
        "cli.sending": "📤 Sending...",
        "cli.done": "✅ Done!",
        "cli.sent_failed": "Sent: {s} | Failed: {f}",
        "cli.stats_title": "📊 Statistics",
        "cli.contacts_l": "👥 Contacts:",
        "cli.campaigns_l": "📢 Campaigns:",
        "cli.total_sends": "📨 Total sends:",
        "cli.statuses": "Statuses",
        "cli.pending": "⏳ Pending:",
        "cli.sent": "✅ Sent:",
        "cli.delivered": "📬 Delivered:",
        "cli.read": "👁 Read:",
        "cli.failed": "❌ Failed:",
        "cli.templates_title": "📋 Templates ({n})",
        "cli.actions": "Actions:",
        "cli.act_create": "Create a template",
        "cli.act_delete": "Delete a template",
        "cli.act_back": "Back",
        "cli.action": "Action",
        "cli.name": "Name: ",
        "cli.category": "Category (general/broadcast/info): ",
        "cli.body_lines": "Message body (empty line to finish):",
        "cli.template_created": "✅ Template '{name}' created!",
        "cli.no_templates": "No templates.",
        "cli.delete_which": "Delete",
        "cli.template_deleted": "✅ Template deleted.",
        "cli.history_title": "📨 Campaign history ({n})",
        "cli.no_campaigns": "No campaigns.",
        "cli.results": "Results: {s} sent, {f} failed",
        "cli.test_title": "💬 Test a WhatsApp message",
        "cli.number_ex": "Number (e.g.: +14155238886): ",
        "cli.sending2": "Sending...",
        "cli.msg_sent": "✅ Message sent! SID: {sid}",
        "cli.send_failed": "❌ Failed: {err}",
        "cli.delete_confirm": "Delete {n} contacts? (yes/no): ",
        "cli.no_contacts_del": "No contacts to delete.",
        "cli.deleted_n": "✅ {n} contacts deleted.",
        "cli.goodbye": "Goodbye! 👋",
        "cli.invalid_option": "Invalid option.",
        "cli.contacts_suffix": "contacts",
        # =========================================================
        # CSV handler
        # =========================================================
        "csv.decode_error": "Could not decode the CSV file.",
        "csv.parse_error": "CSV parsing error: {e}",
        "csv.empty": "The CSV file is empty.",
        "csv.no_phone_col": "No 'phone' column found. Available columns: {cols}",
        "csv.invalid_number": "Line {n}: invalid number '{phone}'",
        # =========================================================
        # main.py (console)
        # =========================================================
        "main.packages_missing": "❌ Missing packages: {pkgs}",
        "main.install_hint": "   Install with: pip install -r requirements.txt",
        "main.telegram_token_required": "TELEGRAM_BOT_TOKEN is required for Telegram mode",
        "main.epilog": (
            "\nModes:\n"
            "  (default)     Interactive CLI — CSV import, bulk send\n"
            "  --telegram    Telegram bot + webhook server\n"
            "  --webhook     Webhook server only (delivery callbacks)\n"
            "  --dashboard   Streamlit web dashboard\n"
            "\nExamples:\n"
            "  python main.py                    # CLI\n"
            "  python main.py --telegram         # Production\n"
            "  python main.py --dashboard        # Web UI\n"
            "  python main.py --webhook          # API only\n        "
        ),
        "main.help_telegram": "Run the Telegram bot + webhook",
        "main.help_webhook": "Run the webhook server only",
        "main.help_dashboard": "Run the Streamlit dashboard",
        # =========================================================
        # Configuration warnings
        # =========================================================
        "cfgw.twilio_sid": "TWILIO_ACCOUNT_SID not set",
        "cfgw.twilio_token": "TWILIO_AUTH_TOKEN not set",
        "cfgw.twilio_from": "TWILIO_WHATSAPP_FROM not set",
        "cfgw.meta_phone_id": "WA_PHONE_NUMBER_ID not set",
        "cfgw.meta_token": "WA_ACCESS_TOKEN not set",
        "cfgw.gowa_url": "GOWA_BASE_URL not set",
        "cfgw.gowa_auth": "GOWA_USERNAME / GOWA_PASSWORD not set (GoWA Basic Auth)",
        "cfgw.unknown_provider": "Unknown WA_PROVIDER: {p} (use 'twilio', 'meta' or 'gowa')",
        "cfgw.rate": "WA_MESSAGES_PER_SECOND is {v}, should be > 0",
        # =========================================================
        # Telegram bot
        # =========================================================
        "tg.access_denied": "⛔ Access denied. Admins only.",
        "tg.start_admin": "Administration panel.",
        "tg.provider": "Provider: {p}",
        "tg.btn_stats": "📊 Stats",
        "tg.btn_contacts": "👥 Contacts",
        "tg.btn_campaigns": "📢 Campaigns",
        "tg.btn_templates": "📋 Templates",
        "tg.btn_help": "ℹ️ Help",
        "tg.stats_title": "📊 <b>Statistics</b>",
        "tg.contacts_l": "👥 Contacts: {n}",
        "tg.campaigns_l": "📢 Campaigns: {n}",
        "tg.total_sends": "📨 Total sends: {n}",
        "tg.statuses": "<b>Statuses</b>",
        "tg.pending": "⏳ Pending: {n}",
        "tg.sent": "✅ Sent: {n}",
        "tg.delivered": "📬 Delivered: {n}",
        "tg.read": "👁 Read: {n}",
        "tg.failed": "❌ Failed: {n}",
        "tg.no_contacts_use_import": "No contacts. Use /import to import a CSV.",
        "tg.contacts_title": "👥 <b>Contacts</b> ({n})",
        "tg.and_more": "... and {n} more",
        "tg.no_templates": "No templates.",
        "tg.templates_title": "📋 <b>Templates</b> ({n})",
        "tg.import_title": "📥 <b>CSV Import</b>",
        "tg.import_instructions": (
            "Send me a CSV file with columns:\n"
            "<code>phone</code>, <code>first_name</code>, <code>last_name</code> (optional), <code>email</code> (optional)\n\n"
            "Type /cancel to abort."
        ),
        "tg.send_csv": "Send a .csv file",
        "tg.import_failed": "❌ Import failed:\n{err}",
        "tg.no_valid": "No valid contacts.",
        "tg.found_n": "✅ {n} contacts found.",
        "tg.errors_n": "\n⚠ {n} errors.",
        "tg.name_campaign": "\n\nName this campaign:",
        "tg.no_contacts_import": "❌ No contacts to import.",
        "tg.importing": "⏳ Importing...",
        "tg.import_done": "✅ <b>Import complete</b>",
        "tg.campaign": "Campaign: {name}",
        "tg.contacts": "Contacts: {n}",
        "tg.use_send": "Use /send to send messages.",
        "tg.no_campaign_use_import": "No campaigns. Use /import first.",
        "tg.choose_campaign": "📢 <b>Choose a campaign:</b>",
        "tg.campaign_invalid": "Invalid campaign.",
        "tg.no_contacts_in_campaign": "No contacts in this campaign.",
        "tg.recipients_n": (
            "📝 {n} recipients.\n\n"
            "Write your message or pick a template.\n"
            "Placeholders: {{first_name}}, {{last_name}}, {{phone}}\n\n"
        ),
        "tg.templates": "Templates:\n",
        "tg.cancel_hint": "\nType /cancel to abort.",
        "tg.template_not_found": "Template '{name}' not found.",
        "tg.preview": "📋 Preview:\n\n{preview}\n\nRecipients: {n}\n\nConfirm? Type yes or /cancel",
        "tg.send_cancelled": "❌ Send cancelled.",
        "tg.missing_data": "❌ Missing data.",
        "tg.sending_n": "📤 Sending... 0/{n}",
        "tg.sending_progress": "📤 Sending... {c}/{t}",
        "tg.send_done": "✅ <b>Send complete</b>\n\nSent: {s}\nFailed: {f}",
        "tg.cancelled": "❌ Cancelled.",
        "tg.stats_inline": (
            "📊 <b>Statistics</b>\n\n"
            "👥 Contacts: {c}\n📢 Campaigns: {n}\n\n"
            "✅ Sent: {s} | 📬 Delivered: {d} | ❌ Failed: {f}"
        ),
        "tg.no_contacts_short": "No contacts. Use /import.",
        "tg.no_campaign_short": "No campaigns. Use /import.",
        "tg.campaigns_title": "📢 <b>Campaigns</b> ({n})",
        "tg.contacts_suffix": "contacts",
        "tg.help": (
            "ℹ️ <b>Commands</b>\n\n"
            "/start — Main menu\n"
            "/stats — Statistics\n"
            "/contacts — Contact list\n"
            "/import — Import CSV\n"
            "/send — Send a campaign\n"
            "/templates — Templates\n"
        ),
    },
}


def t(key: str, lang: str | None = None) -> str:
    """Return the translated string for *key*.

    Falls back to English, then to the key itself if missing everywhere.
    """
    l = (lang or APP_LANG).lower()
    if l not in STRINGS:
        l = "id"
    val = STRINGS[l].get(key)
    if val is None:
        val = STRINGS["en"].get(key, key)
    return val


def available_languages() -> list[tuple[str, str]]:
    """Return [(code, label), ...] for the language picker."""
    return [("id", "🇮🇩 Indonesia"), ("en", "🇬🇧 English")]
