from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EsimInfo:
    """Normalized eSIM record returned by any provider adapter."""

    found: bool = False
    provider: str = ""
    iccid: str | None = None
    status: str | None = None  # e.g. not_installed | active | expired | unknown
    qr_code: str | None = None  # QR payload (LPA string) or activation code
    smdp_address: str | None = None
    activation_code: str | None = None
    data_total_mb: float | None = None
    data_used_mb: float | None = None
    data_remaining_mb: float | None = None
    expires_at: str | None = None
    raw: dict = field(default_factory=dict)
    error: str | None = None


class EsimProvider(ABC):
    """Common interface every eSIM provider adapter must implement."""

    name: str = "base"

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 api_secret: str | None = None, extra: dict | None = None) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.extra = extra or {}

    @abstractmethod
    def get_esim(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        ...

    def get_balance(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        # Default: reuse get_esim which already carries balance fields.
        return self.get_esim(iccid=iccid, order_ref=order_ref)

    def get_qr(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        return self.get_esim(iccid=iccid, order_ref=order_ref)
