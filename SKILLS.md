# SKILLS.md — eSIM Support Agent

Source of truth for the skills (callable tools) the AI agent may use. The LLM never
touches the database directly — it only requests a skill with arguments, and the backend
validates them and returns a structured result.

Implemented in `app/agent/tools/` (one file per skill), dispatched by
`app/agent/tools/__init__.py`. Keep this file in sync with that code — see
`.cursor/rules/agent-skills.mdc`.

---

## Skill format

- `name` — function name used in the tool schema
- `description` — one line telling the LLM when to use it
- `input` — parameters + type
- `output` — what gets returned to the LLM
- `guardrail` — hard rule the backend enforces regardless of what the LLM asks for

---

## 1. kb_search

- **description**: Search the knowledge base (RAG) for install/activation, device, plan, and policy info.
- **input**: `query: string`, optional `language: "en" | "ar"`
- **output**: `{ results: [{ text, source, distance }] }`
- **guardrail**: Read-only. Answers must be grounded in returned snippets; if nothing relevant, say so or escalate.

## 2. order_lookup

- **description**: Retrieve order + billing details from WooCommerce by order number, email, or phone.
- **input**: `order_number: string` OR `email: string` OR `phone: string`
- **output**: `{ found: bool, orders: [{ id, number, status, total, currency, items, ... }] }`
- **guardrail**: Read-only. Loose lookup (no identity gate yet) — do not read out full PII unprompted.

## 3. check_esim_usage

- **description**: Check remaining data, total package, expiry, and status for a menasim eSIM by ICCID (menasim.com usage page).
- **input**: `iccid: string` (required)
- **output**: `{ found, iccid, status, remaining, total, expired_at, is_unlimited, ... }`
- **guardrail**: Read-only. Requires verified identity (`order_lookup` first). Never invent usage numbers.

## 4. esim_status

- **description**: Check whether a customer's eSIM is activated/pending/failed (provider adapter or WooCommerce meta).
- **input**: `iccid: string` OR `order_ref: string` OR `order_number` / `email`
- **output**: `{ found: bool, status, iccid, ... }`
- **guardrail**: Read-only. No side effects.

## 5. resend_qr

- **description**: Regenerate and send the eSIM activation QR (as an image) + activation code.
- **input**: `iccid` / `order_ref` (+ `provider`) OR `order_number` / `email`
- **output**: `{ sent: bool, iccid, qr_image_url, activation_code }`
- **guardrail**: **Max 3 resends per conversation per 24h.** The 4th attempt does not resend — it auto-escalates to a human (counted from the `skill_calls` log).

## 6. refund_ticket

- **description**: Log a refund/cancellation request for the billing team. The agent CANNOT issue a refund itself.
- **input**: `reason: string` (required), optional `order_number`, `contact`, `amount: number` (USD)
- **output**: `{ logged: bool, ticket_id, escalated: bool }`
- **guardrail**: **Hard cap — if `amount >= 50` (USD) the request is force-escalated to a human** (never presented as agent-resolvable). Under 50 it is still only a ticket, never an automatic refund. Always sets `needs_human` and notifies the team.

## 7. escalate

- **description**: Hand the conversation to a human agent and pause AI replies.
- **input**: `reason: string` (required), optional `summary`, `contact`
- **output**: `{ escalated: true, ticket_id }`
- **guardrail**: Always available. Creates a ticket, notifies the team (email), and sets the conversation `needs_human` flag (the console handover toggle picks this up).

---

## Planned (not implemented yet)

- **change_plan** — swap a customer's active plan; would require a real provider/WooCommerce write path and a price-increase confirmation guardrail (> $30 needs explicit customer OK).
- **get_conversation_summary** — auto-summarize a chat for a human picking it up on handoff.

Until these ship, the agent uses `escalate` for plan changes and hands raw transcripts to the console.

---

## Global rules (every skill)

1. **All skill calls are logged** to the `skill_calls` table: `conversation_id, skill_name, input, output, created_at` (written centrally in `execute_tool`).
2. The LLM only supplies arguments; the backend validates types/ranges and executes. No skill writes from raw LLM output.
3. Any skill failure returns a structured error to the LLM (`{ "error": "reason" }`) — never a raw stack trace.
4. Money-related guardrails (`refund_ticket` cap; future `change_plan` confirmation) are enforced server-side, not just described in the prompt.
5. If no skill fits what the customer needs, the agent calls `escalate` rather than improvising an action.
