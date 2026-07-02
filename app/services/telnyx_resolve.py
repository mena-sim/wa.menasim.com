from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.services.telnyx_client import normalize_e164

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def looks_like_waba_id(value: str) -> bool:
    v = (value or "").strip()
    return v.isdigit() and len(v) >= 10


def is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match((value or "").strip()))


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def lookup_messaging_profile_for_number(
    client: httpx.Client, api_key: str, phone: str
) -> str | None:
    phone = normalize_e164(phone)
    if not phone:
        return None
    try:
        resp = client.get(
            "https://api.telnyx.com/v2/phone_numbers/messaging",
            params={"filter[phone_number]": phone, "page[size]": 5},
            headers=_headers(api_key),
        )
        if resp.status_code >= 300:
            return None
        for row in resp.json().get("data") or []:
            pid = str((row or {}).get("messaging_profile_id") or "").strip()
            if pid and is_uuid(pid):
                return pid
    except httpx.HTTPError:
        return None
    return None


def _profile_by_id(profiles: list[dict], profile_id: str) -> dict | None:
    for p in profiles:
        if str(p.get("id") or "") == profile_id:
            return p
    return None


def pick_app_messaging_profile(
    profiles: list[dict],
    *,
    configured_profile_id: str,
    app_webhook_url: str,
) -> dict | None:
    """Choose the messaging profile that belongs to THIS app (not voxbulk etc.)."""
    configured = (configured_profile_id or "").strip()
    target = (app_webhook_url or "").rstrip("/")

    if configured and not looks_like_waba_id(configured):
        hit = _profile_by_id(profiles, configured)
        if hit:
            return hit

    for p in profiles:
        wh = str(p.get("webhook_url") or "").rstrip("/")
        if target and wh == target:
            return p

    for p in profiles:
        name = str(p.get("name") or "").lower()
        wh = str(p.get("webhook_url") or "").lower()
        if "voxbulk" in wh:
            continue
        if any(h in name for h in ("wa", "menasim", "wamena")):
            return p

    return None


def effective_profile_id(
    client: httpx.Client,
    api_key: str,
    *,
    configured_profile: str,
    from_number: str,
    app_webhook_url: str = "",
) -> tuple[str | None, list[str]]:
    """Profile used for outbound sends — prefers the app-configured menasim profile."""
    warnings: list[str] = []
    configured = (configured_profile or "").strip()

    if configured and looks_like_waba_id(configured):
        warnings.append(
            "Messaging profile ID is set to your WABA ID — ignoring it for API calls."
        )
        configured = ""

    profiles: list[dict] = []
    try:
        resp = client.get(
            "https://api.telnyx.com/v2/messaging_profiles",
            params={"page[size]": 50},
            headers=_headers(api_key),
        )
        if resp.status_code < 300:
            profiles = list(resp.json().get("data") or [])
    except httpx.HTTPError:
        pass

    app_profile = pick_app_messaging_profile(
        profiles,
        configured_profile_id=configured,
        app_webhook_url=app_webhook_url,
    )
    number_profile_id = lookup_messaging_profile_for_number(client, api_key, from_number)

    if app_profile:
        app_id = str(app_profile.get("id") or "")
        if number_profile_id and number_profile_id != app_id:
            num_p = _profile_by_id(profiles, number_profile_id)
            num_name = (num_p or {}).get("name") or number_profile_id
            warnings.append(
                f"Number {from_number} is on profile '{num_name}' but this app uses "
                f"'{app_profile.get('name')}'. Run Auto-detect to reassign the number."
            )
        return app_id, warnings

    if configured and is_uuid(configured):
        try:
            resp = client.get(
                f"https://api.telnyx.com/v2/messaging_profiles/{configured}",
                headers=_headers(api_key),
            )
            if resp.status_code < 300:
                return configured, warnings
            if resp.status_code == 404:
                warnings.append(
                    f"Messaging profile '{configured}' not found — set the correct profile in Settings."
                )
        except httpx.HTTPError:
            pass

    if number_profile_id:
        warnings.append(
            "No menasim messaging profile configured — falling back to wherever the number is assigned now."
        )
        return number_profile_id, warnings

    return None, warnings


def ensure_profile_webhook(
    client: httpx.Client, api_key: str, profile_id: str, webhook_url: str
) -> tuple[bool, str]:
    """Set messaging profile webhook URL via Telnyx API."""
    try:
        resp = client.patch(
            f"https://api.telnyx.com/v2/messaging_profiles/{profile_id}",
            json={"webhook_url": webhook_url, "webhook_api_version": "2"},
            headers=_headers(api_key),
        )
        if resp.status_code < 300:
            return True, f"Webhook URL set on profile {profile_id}"
        return False, f"Failed to set webhook (HTTP {resp.status_code}): {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return False, f"Webhook update error: {exc}"


def assign_number_to_profile(
    client: httpx.Client, api_key: str, phone: str, profile_id: str
) -> tuple[bool, str]:
    phone = normalize_e164(phone)
    if not phone or not profile_id:
        return False, "Missing phone or profile id"
    try:
        resp = client.patch(
            f"https://api.telnyx.com/v2/phone_numbers/{quote(phone, safe='')}/messaging",
            json={"messaging_profile_id": profile_id},
            headers=_headers(api_key),
        )
        if resp.status_code < 300:
            return True, f"Assigned {phone} to profile {profile_id}"
        return False, f"Number assign failed (HTTP {resp.status_code}): {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return False, f"Number assign error: {exc}"
