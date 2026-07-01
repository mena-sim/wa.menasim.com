# menasim WA support

AI customer-support agent for **menasim eSIM**. It answers install/activation and
troubleshooting questions, looks up orders and eSIM data, resends QR codes, and escalates to
a human when needed. It runs the same agent core over two channels:

- **Web chat** (local testing) — a browser chat UI.
- **WhatsApp via Telnyx** (production) — enabled by setting Telnyx keys.

## Stack

- Python + FastAPI (SQLite via SQLAlchemy, tables auto-created on startup)
- **DeepSeek** LLM (OpenAI-compatible) with function/tool calling
- **RAG knowledge base**: local `sentence-transformers` embeddings + Chroma (no embedding API cost)
- Pluggable **eSIM provider adapters** (Airalo, eSIMcard, eSIMaccess — stubs to fill with real APIs) + **WooCommerce** order reader
- **Telnyx** WhatsApp send + Ed25519 webhook verification

## Quick start (local web tester)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then add DEEPSEEK_API_KEY
uvicorn app.main:app --reload --port 8080
```

Open http://127.0.0.1:8080 and chat. Only `DEEPSEEK_API_KEY` is required to start talking.
Add WooCommerce keys to enable order lookups; add Telnyx keys later for WhatsApp.

First run downloads the local embedding model (`all-MiniLM-L6-v2`) and indexes `kb_docs/`.

## Key endpoints

| Path | Purpose |
|------|---------|
| `GET /` | Web chat tester |
| `GET /inbox` | Agent inbox (escalation/refund tickets + transcripts) |
| `POST /api/chat` | Web chat API |
| `POST /telnyx/webhooks/messages` | WhatsApp inbound (Telnyx) |
| `GET /api/kb/documents`, `POST /api/kb/reindex`, `GET /api/kb/search` | Knowledge base |
| `GET/PUT /api/settings/providers` | Configure eSIM providers at runtime |
| `GET /health`, `/health/db`, `/health/kb` | Health checks |

## Configuration (.env)

See `.env.example`. Highlights:

- `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`
- `WC_BASE_URL`, `WC_CONSUMER_KEY`, `WC_CONSUMER_SECRET`, and `WC_ESIM_*_META` (plugin-specific meta keys for ICCID/QR/status)
- `TELNYX_API_KEY`, `TELNYX_WHATSAPP_FROM`, `TELNYX_MESSAGING_PROFILE_ID`, `TELNYX_WEBHOOK_PUBLIC_KEY`
- `SMTP_*` + `ALERT_EMAIL_TO` for escalation email alerts

## eSIM providers

Adapters live in `app/services/providers/`. Airalo/eSIMcard/eSIMaccess currently return **mock
data** until real API docs/keys are wired. Configure/enable a provider at runtime:

```bash
curl -X PUT http://127.0.0.1:8080/api/settings/providers \
  -H "Content-Type: application/json" \
  -d '{"name":"airalo","enabled":true,"is_default":true,"api_key":"..."}'
```

Add a new provider by creating an adapter class and registering it in
`app/services/providers/registry.py`.

## Going to production (WhatsApp)

1. Set the `TELNYX_*` env keys (WhatsApp sender + messaging profile + webhook public key).
2. Point the Telnyx messaging profile webhook to `https://<host>/telnyx/webhooks/messages`.
3. The webhook verifies the Ed25519 signature and dedupes retries by message id.

> Note: WhatsApp only allows free-form replies within 24h of the customer's last message; outside
> that window an approved template is required.

## Tests

```powershell
pytest
```

## Known limitation

Customer identity is **not** verified before showing order/eSIM data (loose lookup by
email/order number), per current product decision. Add a verification gate before production.
