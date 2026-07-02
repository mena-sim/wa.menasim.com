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


def effective_profile_id(
    client: httpx.Client,
    api_key: str,
    *,
    configured_profile: str,
    from_number: str,
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    configured = (configured_profile or "").strip()

    if configured and looks_like_waba_id(configured):
        warnings.append(
            "Messaging profile ID is set to your WABA ID — ignoring it for API calls."
        )
        configured = ""

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
                    f"Messaging profile '{configured}' not found — trying auto-detect."
                )
        except httpx.HTTPError:
            pass

    resolved = lookup_messaging_profile_for_number(client, api_key, from_number)
    if resolved:
        if configured and configured != resolved:
            warnings.append(f"Using auto-detected messaging profile {resolved}.")
        return resolved, warnings

    if configured and is_uuid(configured):
        return configured, warnings
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
