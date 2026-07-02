from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.engine import handle_message
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.processed_event import ProcessedEvent
from app.services import runtime_config, webhook_log
from app.services.telnyx_resolve import (
    effective_profile_id,
    is_uuid,
    lookup_messaging_profile_for_number,
    looks_like_waba_id,
)
from app.services.channels.whatsapp_telnyx_channel import WHATSAPP_CHANNEL, parse_inbound
from app.services.telnyx_client import normalize_e164, send_whatsapp

logger = get_logger(__name__)


def webhook_url() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/telnyx/webhooks/messages"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _list_whatsapp_numbers(client: httpx.Client, api_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        resp = client.get(
            "https://api.telnyx.com/v2/whatsapp/phone_numbers",
            params={"page[size]": 50},
            headers=_headers(api_key),
        )
        if resp.status_code < 300:
            rows = list(resp.json().get("data") or [])
    except httpx.HTTPError:
        logger.exception("failed to list whatsapp phone numbers")
    return rows


def _list_messaging_profiles(client: httpx.Client, api_key: str) -> list[dict[str, Any]]:
    try:
        resp = client.get(
            "https://api.telnyx.com/v2/messaging_profiles",
            params={"page[size]": 50},
            headers=_headers(api_key),
        )
        if resp.status_code < 300:
            return list(resp.json().get("data") or [])
    except httpx.HTTPError:
        logger.exception("failed to list messaging profiles")
    return []


def discover(db: Session) -> dict[str, Any]:
    """Inspect Telnyx and suggest the correct IDs for this app."""
    api_key = runtime_config.get(db, "telnyx_api_key")
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    configured_profile = (runtime_config.get(db, "telnyx_messaging_profile_id") or "").strip()
    configured_waba = (runtime_config.get(db, "whatsapp_business_id") or "").strip()
    out: dict[str, Any] = {
        "from_number": from_number,
        "configured_profile_id": configured_profile,
        "configured_waba_id": configured_waba,
        "whatsapp_numbers": [],
        "messaging_profiles": [],
        "suggested_profile_id": None,
        "suggested_waba_id": None,
        "warnings": [],
    }
    if not api_key:
        out["warnings"].append("Telnyx API key is not set.")
        return out

    try:
        with httpx.Client(timeout=20.0) as client:
            wa_rows = _list_whatsapp_numbers(client, api_key)
            for row in wa_rows:
                out["whatsapp_numbers"].append(
                    {
                        "phone_number": normalize_e164(str(row.get("phone_number") or "")),
                        "waba_id": str(row.get("waba_id") or ""),
                        "status": str(row.get("status") or ""),
                        "display_name": str(row.get("display_name") or ""),
                    }
                )

            matched_wa = next(
                (r for r in out["whatsapp_numbers"] if r["phone_number"] == from_number),
                None,
            )
            if matched_wa:
                out["suggested_waba_id"] = matched_wa.get("waba_id") or None
            elif from_number and out["whatsapp_numbers"]:
                out["warnings"].append(
                    f"Sender {from_number} was not found in Telnyx WhatsApp numbers."
                )

            profiles = _list_messaging_profiles(client, api_key)
            for p in profiles:
                out["messaging_profiles"].append(
                    {
                        "id": str(p.get("id") or ""),
                        "name": str(p.get("name") or ""),
                        "webhook_url": str(p.get("webhook_url") or ""),
                    }
                )

            if from_number:
                resolved = lookup_messaging_profile_for_number(client, api_key, from_number)
                if resolved:
                    out["suggested_profile_id"] = resolved
                elif configured_profile and is_uuid(configured_profile):
                    # Fall back to configured UUID if it exists in the account.
                    if any(p["id"] == configured_profile for p in out["messaging_profiles"]):
                        out["suggested_profile_id"] = configured_profile

            if configured_profile and looks_like_waba_id(configured_profile):
                out["warnings"].append(
                    f"'{configured_profile}' is your WABA ID (Meta). Put that in WhatsApp → Business account ID, "
                    "not Telnyx → Messaging profile ID."
                )
            elif configured_profile and is_uuid(configured_profile):
                if not any(p["id"] == configured_profile for p in out["messaging_profiles"]):
                    out["warnings"].append(
                        f"Messaging profile '{configured_profile}' was not found in your Telnyx account. "
                        "If this is your Telnyx account ID, use the messaging profile UUID instead."
                    )
                    if out["suggested_profile_id"]:
                        out["warnings"].append(
                            f"Suggested messaging profile for {from_number}: {out['suggested_profile_id']}"
                        )
            elif not configured_profile and out["suggested_profile_id"]:
                out["warnings"].append(
                    f"Messaging profile ID is empty. Suggested: {out['suggested_profile_id']}"
                )

            if configured_waba and out["suggested_waba_id"] and configured_waba != out["suggested_waba_id"]:
                out["warnings"].append(
                    f"Configured WABA ID differs from Telnyx ({out['suggested_waba_id']})."
                )

    except httpx.HTTPError as exc:
        out["warnings"].append(f"Telnyx connection error: {exc}")

    return out


def auto_configure(db: Session) -> dict[str, Any]:
    """Pull the correct messaging profile + WABA ID from Telnyx and wire up webhooks."""
    info = discover(db)
    changed: list[str] = []
    actions: list[str] = []
    api_key = runtime_config.get(db, "telnyx_api_key")
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    target_webhook = webhook_url()
    profile_id = info.get("suggested_profile_id") or (
        runtime_config.get(db, "telnyx_messaging_profile_id") or ""
    ).strip()

    if info.get("suggested_waba_id"):
        runtime_config.set_value(db, "whatsapp_business_id", str(info["suggested_waba_id"]))
        changed.append("whatsapp_business_id")
    if info.get("suggested_profile_id"):
        runtime_config.set_value(db, "telnyx_messaging_profile_id", str(info["suggested_profile_id"]))
        changed.append("telnyx_messaging_profile_id")
        profile_id = str(info["suggested_profile_id"])

    if api_key and profile_id and is_uuid(profile_id):
        try:
            with httpx.Client(timeout=20.0) as client:
                from app.services.telnyx_resolve import assign_number_to_profile, ensure_profile_webhook

                ok, msg = ensure_profile_webhook(client, api_key, profile_id, target_webhook)
                actions.append(msg)
                if from_number:
                    ok2, msg2 = assign_number_to_profile(client, api_key, from_number, profile_id)
                    actions.append(msg2)
                    if ok2:
                        changed.append("number_assigned")
                if ok:
                    changed.append("webhook_url")
        except httpx.HTTPError as exc:
            actions.append(f"Telnyx setup error: {exc}")

    if changed:
        db.commit()

    ok = bool(info.get("suggested_profile_id") or "webhook_url" in changed)
    return {
        "ok": ok,
        "message": (
            "Telnyx WhatsApp configured (IDs + webhook URL)."
            if ok
            else "Could not auto-detect IDs. Check sender number and Telnyx API key."
        ),
        "changed": changed,
        "actions": actions,
        "discover": info,
    }


def status(db: Session) -> dict[str, Any]:
    profile_id = runtime_config.get(db, "telnyx_messaging_profile_id")
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    business_id = runtime_config.get(db, "whatsapp_business_id")
    discovered = discover(db)

    wa_convos = (
        db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.channel == WHATSAPP_CHANNEL)
        ).scalar_one()
        or 0
    )

    return {
        "webhook_url": webhook_url(),
        "whatsapp_enabled": runtime_config.whatsapp_enabled(db),
        "llm_enabled": runtime_config.llm_enabled(db),
        "from_number": from_number,
        "messaging_profile_id": profile_id,
        "business_account_id": business_id,
        "webhook_public_key_set": bool(runtime_config.get(db, "telnyx_webhook_public_key")),
        "whatsapp_conversations": int(wa_convos),
        "processed_webhook_events": int(
            db.execute(select(func.count()).select_from(ProcessedEvent)).scalar_one() or 0
        ),
        "suggested_profile_id": discovered.get("suggested_profile_id"),
        "suggested_waba_id": discovered.get("suggested_waba_id"),
        "warnings": discovered.get("warnings") or [],
        "whatsapp_numbers": discovered.get("whatsapp_numbers") or [],
        "messaging_profiles": discovered.get("messaging_profiles") or [],
        "recent_webhooks": webhook_log.recent(db, limit=15),
    }


def test_connection(db: Session) -> dict[str, Any]:
    """Validate Telnyx API key, messaging profile, webhook URL, and sender number."""
    api_key = runtime_config.get(db, "telnyx_api_key")
    if not api_key:
        return {"ok": False, "message": "Telnyx API key is not set."}

    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    expected_webhook = webhook_url()
    details: list[str] = []
    warnings: list[str] = []

    if not from_number:
        return {"ok": False, "message": "WhatsApp sender number is not set (Settings → WhatsApp)."}
    details.append(f"Sender: {from_number}")

    discovered = discover(db)
    warnings.extend(discovered.get("warnings") or [])

    configured_profile = (runtime_config.get(db, "telnyx_messaging_profile_id") or "").strip()
    if configured_profile and looks_like_waba_id(configured_profile):
        return {
            "ok": False,
            "message": (
                f"'{configured_profile}' is your WABA ID (Meta WhatsApp Business Account). "
                "Move it to Settings → WhatsApp → Business account ID, then click "
                "'Auto-detect from Telnyx' to fill the messaging profile UUID."
            ),
            "warnings": warnings,
            "discover": discovered,
        }

    try:
        with httpx.Client(timeout=20.0) as client:
            ping = client.get(
                "https://api.telnyx.com/v2/messaging_profiles",
                params={"page[size]": 1},
                headers=_headers(api_key),
            )
            if ping.status_code >= 300:
                return {
                    "ok": False,
                    "message": f"Telnyx API key rejected (HTTP {ping.status_code}).",
                }

            profile_id, resolve_warnings = effective_profile_id(
                client,
                api_key,
                configured_profile=configured_profile,
                from_number=from_number,
            )
            warnings.extend(resolve_warnings)

            wa_match = next(
                (
                    r
                    for r in (discovered.get("whatsapp_numbers") or [])
                    if r.get("phone_number") == from_number
                ),
                None,
            )
            if wa_match:
                details.append(f"WhatsApp number status: {wa_match.get('status') or 'unknown'}")
                if wa_match.get("waba_id"):
                    details.append(f"WABA ID: {wa_match['waba_id']}")
            else:
                warnings.append(
                    f"{from_number} is not listed under Telnyx → WhatsApp phone numbers."
                )

            if profile_id:
                prof = client.get(
                    f"https://api.telnyx.com/v2/messaging_profiles/{profile_id}",
                    headers=_headers(api_key),
                )
                if prof.status_code < 300:
                    pdata = prof.json().get("data") or {}
                    portal_webhook = (pdata.get("webhook_url") or "").strip()
                    details.append(f"Messaging profile: {pdata.get('name') or profile_id}")
                    if portal_webhook:
                        details.append(f"Telnyx webhook: {portal_webhook}")
                        if portal_webhook.rstrip("/") != expected_webhook.rstrip("/"):
                            warnings.append(
                                f"Telnyx profile webhook is '{portal_webhook}' but this app expects "
                                f"'{expected_webhook}'."
                            )
                    else:
                        warnings.append("No webhook URL is set on the Telnyx messaging profile.")
                else:
                    warnings.append(
                        f"Could not load messaging profile {profile_id} (HTTP {prof.status_code})."
                    )
            else:
                warnings.append(
                    "No messaging profile could be resolved. Click 'Auto-detect from Telnyx' in Settings."
                )

            if discovered.get("suggested_profile_id") and discovered["suggested_profile_id"] != configured_profile:
                details.append(f"Suggested profile ID: {discovered['suggested_profile_id']}")

    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}

    hard_fail = any(
        "not listed under Telnyx" in w or "WABA ID" in w and "ignoring" not in w
        for w in warnings
    )
    msg = "Telnyx WhatsApp configuration looks good."
    if warnings:
        msg = "Connected, but fix the warnings below (or use Auto-detect)."
    return {
        "ok": not hard_fail,
        "message": msg,
        "details": details,
        "warnings": warnings,
        "webhook_url": expected_webhook,
        "discover": discovered,
    }


def test_send(db: Session, to: str, text: str) -> dict[str, Any]:
    to = normalize_e164(to)
    if not to:
        return {"ok": False, "message": "Enter a destination phone number (E.164)."}
    result = send_whatsapp(db, to, text)
    if result.get("ok"):
        return {
            "ok": True,
            "message": f"Test message sent to {to}.",
            "send": result,
        }
    detail = result.get("detail") or result.get("error") or f"HTTP {result.get('status')}"
    return {
        "ok": False,
        "message": f"Send failed: {detail}",
        "send": result,
    }


def test_inbound(
    db: Session,
    *,
    from_number: str,
    text: str,
    send_reply: bool = False,
) -> dict[str, Any]:
    """Run the same agent pipeline used for real WhatsApp webhooks."""
    from_number = normalize_e164(from_number)
    if not from_number:
        return {"ok": False, "message": "Enter the customer phone number to simulate."}

    body = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": f"admin-test-{from_number}",
                "type": "whatsapp",
                "from": from_number,
                "text": text,
            },
        }
    }
    inbound = parse_inbound(body)
    if inbound is None:
        return {"ok": False, "message": "Could not parse simulated Telnyx webhook payload."}

    result = handle_message(
        db,
        channel=WHATSAPP_CHANNEL,
        sender_id=inbound.sender_id,
        text=inbound.text,
        media_url=inbound.media_url,
        is_image=inbound.is_image,
    )
    out: dict[str, Any] = {
        "ok": True,
        "message": "Agent processed the message on the WhatsApp channel.",
        "conversation_id": result.conversation_id,
        "reply": result.reply,
        "suppressed": result.suppressed,
        "escalated": result.escalated,
        "channel": WHATSAPP_CHANNEL,
        "sender_id": from_number,
    }
    if send_reply and result.reply and not result.suppressed:
        send_result = send_whatsapp(db, from_number, result.reply, media_url=result.media_url)
        out["send"] = send_result
        out["ok"] = bool(send_result.get("ok"))
        if not send_result.get("ok"):
            out["message"] = "Agent replied, but WhatsApp delivery failed."
    return out
