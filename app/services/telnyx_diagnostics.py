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
    find_menasim_profile,
    is_uuid,
    lookup_messaging_profile_for_number,
    looks_like_waba_id,
    MENASIM_MESSAGING_PROFILE_NAME,
    pick_app_messaging_profile,
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
        "number_profile_id": None,
        "number_profile_name": None,
        "number_profile_webhook": None,
        "app_profile_id": None,
        "app_profile_name": None,
        "profile_mismatch": False,
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
            menasim_profile = find_menasim_profile(profiles)
            profile_rows = []
            if menasim_profile:
                profile_rows.append(
                    {
                        "id": str(menasim_profile.get("id") or ""),
                        "name": str(menasim_profile.get("name") or ""),
                        "webhook_url": str(menasim_profile.get("webhook_url") or ""),
                    }
                )
            out["messaging_profiles"] = profile_rows

            target_webhook = webhook_url()
            app_profile = pick_app_messaging_profile(
                profiles,
                configured_profile_id=configured_profile,
                app_webhook_url=target_webhook,
            )
            if app_profile:
                out["app_profile_id"] = app_profile.get("id")
                out["app_profile_name"] = app_profile.get("name")
                out["suggested_profile_id"] = app_profile.get("id")

            number_profile_id = None
            if from_number:
                number_profile_id = lookup_messaging_profile_for_number(client, api_key, from_number)
            if number_profile_id:
                out["number_profile_id"] = number_profile_id
                num_p = next((p for p in profile_rows if p["id"] == number_profile_id), None)
                if num_p:
                    out["number_profile_name"] = num_p.get("name")
                    out["number_profile_webhook"] = num_p.get("webhook_url")

            if (
                out["app_profile_id"]
                and out["number_profile_id"]
                and out["app_profile_id"] != out["number_profile_id"]
            ):
                out["profile_mismatch"] = True
                out["warnings"].append(
                    f"INBOUND ROUTING ISSUE: {from_number} is assigned to profile "
                    f"'{out.get('number_profile_name')}' (webhook: {out.get('number_profile_webhook')}). "
                    f"Messages go there, NOT to {target_webhook}. "
                    f"Click Auto-detect to move the number to '{out.get('app_profile_name')}'."
                )
            elif out.get("app_profile_id"):
                app_wh = next(
                    (p["webhook_url"] for p in profile_rows if p["id"] == out["app_profile_id"]),
                    "",
                )
                if app_wh.rstrip("/") != target_webhook.rstrip("/"):
                    out["warnings"].append(
                        f"Profile '{out.get('app_profile_name')}' webhook is '{app_wh}' — "
                        f"Auto-detect will set it to {target_webhook}."
                    )

            if configured_profile and looks_like_waba_id(configured_profile):
                out["warnings"].append(
                    f"'{configured_profile}' is your WABA ID (Meta). Put that in WhatsApp → Business account ID, "
                    "not Telnyx → Messaging profile ID."
                )
            elif not menasim_profile:
                out["warnings"].append(
                    f"Telnyx profile '{MENASIM_MESSAGING_PROFILE_NAME}' was not found. "
                    "SMS/voxbulk/ai-assistant profiles are ignored by this app."
                )
            elif not configured_profile and out["suggested_profile_id"]:
                out["warnings"].append(
                    f"Run Auto-detect to wire {from_number} to '{MENASIM_MESSAGING_PROFILE_NAME}'."
                )

            if configured_waba and out["suggested_waba_id"] and configured_waba != out["suggested_waba_id"]:
                out["warnings"].append(
                    f"Configured WABA ID differs from Telnyx ({out['suggested_waba_id']})."
                )

    except httpx.HTTPError as exc:
        out["warnings"].append(f"Telnyx connection error: {exc}")

    return out


def auto_configure(db: Session) -> dict[str, Any]:
    """Wire menasim profile: correct IDs, webhook URL, and number assignment."""
    info = discover(db)
    changed: list[str] = []
    actions: list[str] = []
    api_key = runtime_config.get(db, "telnyx_api_key")
    from_number = normalize_e164(runtime_config.get(db, "telnyx_whatsapp_from"))
    target_webhook = webhook_url()

    profile_id = info.get("app_profile_id") or info.get("suggested_profile_id")

    if info.get("suggested_waba_id"):
        runtime_config.set_value(db, "whatsapp_business_id", str(info["suggested_waba_id"]))
        changed.append("whatsapp_business_id")
    if profile_id and is_uuid(str(profile_id)):
        runtime_config.set_value(db, "telnyx_messaging_profile_id", str(profile_id))
        changed.append("telnyx_messaging_profile_id")

    if api_key and profile_id and is_uuid(str(profile_id)):
        try:
            with httpx.Client(timeout=20.0) as client:
                from app.services.telnyx_resolve import assign_number_to_profile, ensure_profile_webhook

                ok, msg = ensure_profile_webhook(client, api_key, str(profile_id), target_webhook)
                actions.append(msg)
                if from_number:
                    ok2, msg2 = assign_number_to_profile(
                        client, api_key, from_number, str(profile_id)
                    )
                    actions.append(msg2)
                    if ok2:
                        changed.append("number_assigned")
                if ok:
                    changed.append("webhook_url")
        except httpx.HTTPError as exc:
            actions.append(f"Telnyx setup error: {exc}")

    if changed:
        db.commit()

    ok = bool(profile_id and is_uuid(str(profile_id)))
    return {
        "ok": ok,
        "message": (
            f"Moved {from_number} to profile '{info.get('app_profile_name')}' "
            f"and set webhook to {target_webhook}."
            if ok and info.get("profile_mismatch")
            else (
                "Telnyx WhatsApp profile configured."
                if ok
                else f"Profile '{MENASIM_MESSAGING_PROFILE_NAME}' not found in Telnyx — create it, then retry Auto-detect."
            )
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
        "app_profile_id": discovered.get("app_profile_id"),
        "app_profile_name": discovered.get("app_profile_name"),
        "number_profile_id": discovered.get("number_profile_id"),
        "number_profile_name": discovered.get("number_profile_name"),
        "number_profile_webhook": discovered.get("number_profile_webhook"),
        "profile_mismatch": discovered.get("profile_mismatch"),
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
                app_webhook_url=expected_webhook,
            )
            warnings.extend(resolve_warnings)

            if discovered.get("profile_mismatch"):
                warnings.append(
                    "Your WhatsApp number is still routed to another service (voxbulk). "
                    "Click Auto-detect to fix inbound routing."
                )

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
                    details.append(
                        f"App profile: {pdata.get('name') or profile_id} ({profile_id})"
                    )
                    if portal_webhook:
                        details.append(f"App profile webhook: {portal_webhook}")
                        if portal_webhook.rstrip("/") != expected_webhook.rstrip("/"):
                            warnings.append(
                                f"Profile '{pdata.get('name')}' webhook is '{portal_webhook}'. "
                                f"Click Auto-detect to set '{expected_webhook}'."
                            )
                    else:
                        warnings.append("No webhook on menasim profile — run Auto-detect.")
                else:
                    warnings.append(
                        f"Could not load messaging profile {profile_id} (HTTP {prof.status_code})."
                    )
            else:
                warnings.append(
                    "No WA 2-99 profile found — run Auto-detect after the profile exists in Telnyx."
                )

            if discovered.get("number_profile_name"):
                details.append(
                    f"Number currently on: {discovered['number_profile_name']} "
                    f"({discovered.get('number_profile_webhook') or 'no webhook'})"
                )

    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Connection error: {exc}"}

    hard_fail = any(
        "not listed under Telnyx" in w
        or ("WABA ID" in w and "ignoring" not in w)
        or "INBOUND ROUTING ISSUE" in w
        or discovered.get("profile_mismatch")
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
