# INSTALL.md — Menasim WA Support (Ubuntu 24.04 + aaPanel)

Production deploy for the eSIM support agent. Assumes **aaPanel** is installed (Nginx,
MySQL, and the Python Project Manager come with it). Only the extra bits are listed.

Server layout (as agreed):

| Piece | Path / host |
|-------|-------------|
| App code (API + built admin) | `/www/wamena` |
| Chat/API domain | `wa.menasim.com` → reverse proxy to `127.0.0.1:8000` |
| Admin domain | `adminwa.menasim.com` → reverse proxy to `127.0.0.1:8000` (serves `/admin`) |
| Static site root (optional) | `/www/wwwroot/wa.menasim.com` |

The admin is a static build served by FastAPI at `/admin`, so both domains point at the
same app; you don't need a separate Node process in production.

---

## 1. System packages (only what aaPanel doesn't already provide)

```bash
sudo apt-get update
# ffmpeg is required for voice-note transcription (Whisper/DeepInfra)
sudo apt-get install -y ffmpeg
# Python 3.12 venv support (usually present on 24.04)
sudo apt-get install -y python3.12-venv build-essential
ffmpeg -version   # sanity check
```

Node.js (to build the admin once) — install via aaPanel **App Store → Node.js** (v18+),
or:

```bash
sudo apt-get install -y nodejs npm
```

## 2. MySQL database (utf8mb4 for Arabic + emoji)

In aaPanel → **Databases → Add Database**, or via CLI:

```sql
CREATE DATABASE wa_menasim CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wa_menasim'@'127.0.0.1' IDENTIFIED BY 'STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON wa_menasim.* TO 'wa_menasim'@'127.0.0.1';
FLUSH PRIVILEGES;
```

`utf8mb4` matters — Arabic messages and emoji break on plain `utf8`.

## 3. Get the code

```bash
cd /www
git clone https://github.com/mena-sim/wa.menasim.com.git wamena
cd /www/wamena
```

## 4. Python environment

```bash
cd /www/wamena
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # includes PyMySQL for MySQL
```

## 5. Configuration (`.env`)

```bash
cp .env.example .env
nano .env
```

Set at least:

```ini
DATABASE_URL=mysql+pymysql://wa_menasim:STRONG_PASSWORD@127.0.0.1:3306/wa_menasim?charset=utf8mb4
PUBLIC_BASE_URL=https://wa.menasim.com
ADMIN_PASSWORD=change-me
ENCRYPTION_KEY=            # leave blank -> auto-generated at data/secret.key on first run
DEEPSEEK_API_KEY=sk-...    # or set later in the admin UI
```

Everything else (Telnyx, WooCommerce, SMTP, DeepInfra voice) can be filled in later from
the **admin console** — those are stored encrypted in MySQL and override `.env`.

Tables are created automatically on first startup (`init_db()`), so no manual migration
step is needed.

## 6. Build the admin console (one time, and after UI changes)

```bash
cd /www/wamena/admin-web
npm install
npm run build            # outputs admin-web/dist, served by FastAPI at /admin
```

## 7. Run the API (aaPanel Python Project Manager)

In aaPanel → **App Store → Python Project Manager → Add Project**:

- Project path: `/www/wamena`
- Python version / venv: `/www/wamena/.venv`
- Startup: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
- Set it to run on boot.

CLI equivalent for a quick test:

```bash
cd /www/wamena
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check: `curl http://127.0.0.1:8000/health`.

## 8. Nginx reverse proxies (2 sites in aaPanel)

Create two sites in aaPanel and add SSL (Let's Encrypt) to each.

**Site A — `wa.menasim.com`** (chat widget + Telnyx webhook + API):

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Site B — `adminwa.menasim.com`** (admin console):

```nginx
location / {
    proxy_pass http://127.0.0.1:8000/admin/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location /api/ {
    proxy_pass http://127.0.0.1:8000/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location /media/ {
    proxy_pass http://127.0.0.1:8000/media/;
}
```

Then open `https://adminwa.menasim.com` and sign in with `ADMIN_PASSWORD`.

## 9. Telnyx webhook

In the Telnyx portal, set the messaging profile webhook to:

```
https://wa.menasim.com/telnyx/webhooks/messages
```

Paste the **Telnyx API key**, **messaging profile ID**, **webhook public key**, and your
**WhatsApp sender number** in the admin (Settings → Telnyx / WhatsApp), then **Save** and
**Test connection**.

## 9b. Twilio mobile number (SMS)

SMS is independent of the WhatsApp provider. You can keep Telnyx/Meta for WhatsApp and still
receive customer texts on a Twilio number.

1. In **Twilio Console → Account → API keys & tokens**, copy Account SID and Auth Token.
2. Buy or port a number under **Phone Numbers → Manage → Buy a number** (enable **SMS**).
3. In admin **Settings → Twilio**, paste SID + token, **Save**, then **Load numbers from Twilio**.
4. Click **Use for SMS** on the number. That sets Twilio’s inbound webhook to:

```
https://wa.menasim.com/twilio/webhooks/sms
```

   You can also paste that URL yourself: open the number → Messaging → “A message comes in”
   → Webhook, HTTP POST.

5. Enable inbound SMS, **Save SMS settings**, then text the number. The chat appears in
   **Conversations** with channel `sms`.

If the number belongs to a **Messaging Service**, set the service inbound URL to the same
webhook (the per-number `SmsUrl` is ignored).

WhatsApp via Twilio is separate: set the WhatsApp sender on the same tab, then choose
**Twilio** as the active provider on the WhatsApp tab. WhatsApp webhook:

```
https://wa.menasim.com/twilio/webhooks/whatsapp
```

## 10. First-run checklist

- [ ] `/health` returns `ok`
- [ ] Admin login works at `adminwa.menasim.com`
- [ ] DeepSeek test passes (Settings → DeepSeek)
- [ ] WooCommerce test passes (Settings → WordPress API)
- [ ] Upload/verify knowledge base (Settings → Agent & KB → Rebuild index)
- [ ] Import old WhatsApp `.txt` history (Settings → Agent & KB → Import WhatsApp history)
- [ ] Telnyx test passes and webhook is set
- [ ] (Optional) Twilio SMS: number loaded, inbound webhook set, test SMS received
- [ ] Send a test WhatsApp message and confirm it appears in **Conversations**

## Updating later

```bash
cd /www/wamena
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
cd admin-web && npm install && npm run build && cd ..
# restart the project from aaPanel Python Project Manager
```
