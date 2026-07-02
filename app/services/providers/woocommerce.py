from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services import runtime_config

logger = get_logger(__name__)


def _csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


class WooCommerceClient:
    """Read-only WooCommerce REST client for orders + eSIM order meta.

    Uses HTTP Basic auth with consumer key/secret (recommended over HTTPS).
    eSIM meta keys are plugin-dependent, so the meta key names are configurable
    at runtime (WC_ESIM_*_META).
    """

    def __init__(self, db: Session) -> None:
        self.base_url = (runtime_config.get(db, "wc_base_url") or "").rstrip("/")
        self.key = runtime_config.get(db, "wc_consumer_key")
        self.secret = runtime_config.get(db, "wc_consumer_secret")
        self.iccid_keys = _csv(runtime_config.get(db, "wc_esim_iccid_meta"))
        self.qr_keys = _csv(runtime_config.get(db, "wc_esim_qr_meta"))
        self.status_keys = _csv(runtime_config.get(db, "wc_esim_status_meta"))

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.key and self.secret)

    def _api_root(self) -> str:
        root = self.base_url
        if root.endswith("/wp-json"):
            root = root[: -len("/wp-json")]
        return root

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._api_root()}/wp-json/wc/v3/{path.lstrip('/')}"
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params=params or {}, auth=(self.key, self.secret))
            resp.raise_for_status()
            return resp.json()

    def test_connection(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Missing base URL, consumer key or secret."
        try:
            self._get("orders", {"per_page": 1})
            return True, "Connected to WooCommerce."
        except httpx.HTTPStatusError as exc:
            return False, f"HTTP {exc.response.status_code}: check keys/permissions."
        except httpx.HTTPError as exc:
            return False, f"Connection error: {exc}"

    def find_orders(
        self,
        *,
        email: str | None = None,
        order_number: str | None = None,
        phone: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            if order_number:
                num = str(order_number).lstrip("#").strip()
                try:
                    return [self._get(f"orders/{int(num)}")]
                except (ValueError, httpx.HTTPStatusError):
                    return self._get("orders", {"search": num, "per_page": 5})
            if email:
                return self._get("orders", {"search": email, "per_page": 5})
            if phone:
                return self._get("orders", {"search": phone, "per_page": 5})
        except httpx.HTTPError as exc:
            logger.warning("woocommerce find_orders error: %s", exc)
            return []
        return []

    def _meta_value(self, meta: list[dict[str, Any]], keys: list[str]) -> str | None:
        by_key = {str(m.get("key", "")).lower(): m.get("value") for m in meta if isinstance(m, dict)}
        for k in keys:
            val = by_key.get(k.lower())
            if val:
                return str(val)
        return None

    def extract_esim(self, order: dict[str, Any]) -> dict[str, Any]:
        """Pull eSIM fields from an order (top-level meta + line-item meta)."""
        meta: list[dict[str, Any]] = list(order.get("meta_data") or [])
        for item in order.get("line_items") or []:
            meta.extend(item.get("meta_data") or [])
        return {
            "iccid": self._meta_value(meta, self.iccid_keys),
            "qr": self._meta_value(meta, self.qr_keys),
            "status": self._meta_value(meta, self.status_keys),
        }

    @staticmethod
    def summarize_order(order: dict[str, Any]) -> dict[str, Any]:
        billing = order.get("billing") or {}
        items = [li.get("name") for li in (order.get("line_items") or []) if li.get("name")]
        return {
            "id": order.get("id"),
            "number": order.get("number"),
            "status": order.get("status"),
            "date": order.get("date_created"),
            "total": order.get("total"),
            "currency": order.get("currency"),
            "email": billing.get("email"),
            "phone": billing.get("phone"),
            "name": f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip(),
            "items": items,
        }
