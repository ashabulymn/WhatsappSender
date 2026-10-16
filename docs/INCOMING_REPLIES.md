# Balasan Masuk — Dokumentasi

Fitur pencatatan otomatis balasan WhatsApp yang masuk ke perangkat wasend
(misalnya nomor broadcast/spam). Setiap balasan teks yang diterima perangkat
disimpan ke database; balasan berisi tepat `STOP` otomatis ditambahkan ke
daftar blokir pemilik perangkat. Tidak perlu pantau manual.

## Arsitektur

```
WhatsApp ──► GoWA ──(webhook per-device, HMAC-SHA256)──► wasend :8000/gowa-incoming
                                                                    │
                                              ┌─────────────────────┴─────────────────────┐
                                              ▼                                           ▼
                                   incoming_replies (SQLite)              blocked_numbers (jika "STOP")
                                              │
                                              ▼
                                   Dashboard → halaman "📨 Balasan Masuk"
                                   (filter, tandai ditangani, ekspor CSV)
```

1. GoWA menerima pesan masuk di perangkat (slot `wasend-<username>`).
2. GoWA mengirim HTTP POST ke `webhook_url` perangkat dengan header
   `X-Hub-Signature-256: sha256=<hmac>` (HMAC-SHA256 dari body mentah).
3. Endpoint `/gowa-incoming` memverifikasi signature, lalu:
   - hanya memproses event `message` yang **bukan** dari diri sendiri
     (`is_from_me = false`) dan berisi teks;
   - menormalkan nomor pengirim dari JID (`628xx@s.whatsapp.net` → `+628xx`);
   - mencari `user_id` pemilik perangkat lewat tabel `user_devices`
     (kolom `gowa_device_id` dicocokkan dengan `session_id` dari GoWA);
     jika tidak ketemu, balasan diabaikan dan dicatat di log;
   - menyimpan ke tabel `incoming_replies`;
   - jika isi pesan (setelah trim, huruf besar) tepat `STOP`, nomor
     ditambahkan ke `blocked_numbers` milik user tersebut.
4. Dashboard menampilkan semuanya di halaman **📨 Balasan Masuk**.

## Environment variables

| Variabel | Default | Keterangan |
|---|---|---|
| `GOWA_WEBHOOK_SECRET` | *(kosong)* | Secret HMAC untuk verifikasi signature. **Wajib sama** dengan `--webhook-secret` / `WHATSAPP_WEBHOOK_SECRET` di server GoWA. Jika kosong, semua webhook ditolak. |
| `WEBHOOK_PORT` | `8000` | Port FastAPI penerima webhook. |
| `DASHBOARD_PORT` | `8501` | Port dashboard Streamlit. |
| `WEBHOOK_HOST` | `0.0.0.0` | Bind address FastAPI. |

Buat secret acak baru (jangan pakai default `secret` milik GoWA):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Menjalankan lokal

```bash
bash start.sh
# webhook  : http://localhost:8000/gowa-incoming
# dashboard: http://localhost:8501
```

## Mendaftarkan webhook per-device di GoWA

Setiap perangkat wasend punya slot device `wasend-<username>` di GoWA.
Daftarkan URL webhook publik untuk tiap slot yang dipakai broadcast:

```bash
# Ganti: GOWA_USER, GOWA_PASS, DEVICE_ID, dan URL publik webhook Anda
curl -u "GOWA_USER:GOWA_PASS" \
  -X PATCH "https://whatsapp.masyamin.biz.id/devices/DEVICE_ID/webhook" \
  -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://wasend.example.com:8000/gowa-incoming"}'
```

Cek konfigurasi yang terpasang:

```bash
curl -u "GOWA_USER:GOWA_PASS" \
  "https://whatsapp.masyamin.biz.id/devices/DEVICE_ID/webhook"
```

Kosongkan `webhook_url` (`""`) untuk menghapus dan kembali ke webhook global.

> Catatan: signature perangkat ditandatangani dengan secret perangkat/global
> GoWA — pastikan `GOWA_WEBHOOK_SECRET` di wasend sama dengan secret yang
> dipakai GoWA menandatangani request tersebut.

## Cara verifikasi

1. Pastikan `start.sh` berjalan dan `GOWA_WEBHOOK_SECRET` terisi di kedua sisi.
2. Daftarkan webhook untuk slot device broadcast (lihat perintah di atas).
3. Dari nomor HP lain, kirim pesan `STOP` ke nomor broadcast.
4. Cek log uvicorn: harus ada baris `stored reply from +62... (stop=True)`.
5. Buka dashboard → **📨 Balasan Masuk**: baris baru muncul dengan badge 🛑 STOP.
6. Buka halaman Kontak → Daftar blokir: nomor tersebut sudah terblokir otomatis.
7. Kirim pesan biasa (bukan STOP) → tersimpan tanpa masuk daftar blokir.

## Catatan deployment Dokploy

- Container kini menjalankan **dua proses** via `start.sh`: uvicorn di
  `WEBHOOK_PORT` (default 8000) dan Streamlit di `DASHBOARD_PORT` (default 8501).
  `Dockerfile` sudah `EXPOSE 8501 8000`.
- Di Dokploy, tambahkan **port mapping / domain kedua** untuk port 8000 agar
  GoWA (server eksternal) bisa mencapai `/gowa-incoming`. Contoh pola umum:
  - domain utama `wasend.masyamin.biz.id` → port 8501 (dashboard), dan
  - domain/port tambahan → port 8000 (webhook), mis. `wasend-hook.masyamin.biz.id`.
- URL yang didaftarkan ke GoWA **harus HTTPS publik** yang mengarah ke port 8000.
- Jangan expose port 8000 tanpa secret: endpoint menolak request tanpa signature
  yang valid, tetapi secret yang kuat tetap wajib.
- Database SQLite tetap di volume persisten (`/app/data`) — tidak ada perubahan.
