from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.services.telnyx_client import normalize_e164

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Only this Telnyx messaging profile is used for menasim WhatsApp (ignore SMS/voxbulk/ai-assistant).
MENASIM_MESSAGING_PROFILE_NAME = "WA 2-99"


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


def _is_menasim_profile_name(name: str) -> bool:
    return str(name or "").strip().casefold() == MENASIM_MESSAGING_PROFILE_NAME.casefold()


def find_menasim_profile(profiles: list[dict]) -> dict | None:
    """Return the WA 2-99 profile only — never SMS, voxbulk, or ai-assistant profiles."""
    for p in profiles:
        if _is_menasim_profile_name(str(p.get("name") or "")):
            return p
    return None


def pick_app_messaging_profile(
    profiles: list[dict],
    *,
    configured_profile_id: str,
    app_webhook_url: str,
) -> dict | None:
    """Choose the menasim WA 2-99 messaging profile (ignore all other Telnyx profiles)."""
    menasim = find_menasim_profile(profiles)
    if not menasim:
        return None

    configured = (configured_profile_id or "").strip()
    if configured and not looks_like_waba_id(configured):
        hit = _profile_by_id(profiles, configured)
        if hit and _is_menasim_profile_name(str(hit.get("name") or "")):
            return hit
        if configured == str(menasim.get("id") or ""):
            return menasim

    return menasim


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
        hit = _profile_by_id(profiles, configured)
        if hit and not _is_menasim_profile_name(str(hit.get("name") or "")):
            warnings.append(
                f"Configured profile '{hit.get('name')}' is not '{MENASIM_MESSAGING_PROFILE_NAME}' — "
                "this app only uses the WA 2-99 profile."
            )

    warnings.append(
        f"Messaging profile '{MENASIM_MESSAGING_PROFILE_NAME}' was not found in your Telnyx account. "
        "Create it in Telnyx or run Auto-detect after it exists."
    )
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
