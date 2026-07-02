from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from app.agent.tools import identity
from app.agent.tools.context import ToolContext

_ICCID_RE = re.compile(r"^89\d{17,18}$")
_USAGE_URL = "https://menasim.com/wp-admin/admin-ajax.php"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_esim_usage",
        "description": (
            "Check remaining data, total package size, expiry, and status for a menasim eSIM "
            "by ICCID. Use when the customer asks how much data is left, if the plan expired, "
            "or package balance. Requires verified identity."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "iccid": {
                    "type": "string",
                    "description": "The eSIM ICCID (19–20 digits, starts with 89).",
                },
            },
            "required": ["iccid"],
        },
    },
}


def _normalize_iccid(value: str) -> str:
    return re.sub(r"\D", "", (value or "").strip())


def _parse_usage_html(html: str) -> dict[str, str]:
    """Parse the HTML table returned by menasim fetch_sim_usage."""
    rows: dict[str, str] = {}
    for m in re.finditer(
        r"<tr>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]*)</td>\s*</tr>",
        html,
        re.I | re.S,
    ):
        key = unescape(m.group(1)).strip().lower()
        val = unescape(m.group(2)).strip()
        if key:
            rows[key] = val
    return rows


def run(ctx: ToolContext, iccid: str | None = None) -> dict[str, Any]:
    if not identity.is_verified(ctx):
        return identity.unverified_result()

    iccid_norm = _normalize_iccid(iccid or "")
    if not iccid_norm or not _ICCID_RE.match(iccid_norm):
        return {
            "found": False,
            "message": "Provide a valid ICCID (19–20 digits, starts with 89).",
        }

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(
                _USAGE_URL,
                params={"action": "fetch_sim_usage", "iccid": iccid_norm},
            )
            if resp.status_code >= 300:
                return {
                    "found": False,
                    "message": f"Usage lookup failed (HTTP {resp.status_code}).",
                }
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"found": False, "message": f"Could not reach menasim usage service: {exc}"}

    if not payload.get("success"):
        return {
            "found": False,
            "iccid": iccid_norm,
            "message": "No usage data found for this ICCID.",
        }

    data_html = str(payload.get("data") or "")
    rows = _parse_usage_html(data_html)
    if not rows:
        return {
            "found": False,
            "iccid": iccid_norm,
            "message": "Usage service returned no readable data for this ICCID.",
        }

    return {
        "found": True,
        "iccid": iccid_norm,
        "status": rows.get("status"),
        "remaining": rows.get("remaining"),
        "total": rows.get("total"),
        "expired_at": rows.get("expired_at"),
        "is_unlimited": rows.get("is_unlimited"),
        "remaining_voice": rows.get("remaining_voice"),
        "remaining_text": rows.get("remaining_text"),
        "source": f"https://menasim.com/check-esims-usage/?iccid={iccid_norm}",
    }
