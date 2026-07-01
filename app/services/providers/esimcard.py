from __future__ import annotations

from app.core.logging import get_logger
from app.services.providers.base import EsimInfo, EsimProvider

logger = get_logger(__name__)


class EsimCardProvider(EsimProvider):
    """eSIMcard API adapter.

    STUB: returns mock data until real credentials + endpoint mapping are supplied.
    """

    name = "esimcard"

    def get_esim(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        if not self.api_key:
            return EsimInfo(
                found=False,
                provider=self.name,
                error="eSIMcard provider not configured (missing API key).",
            )
        logger.info("[esimcard] stub get_esim iccid=%s order_ref=%s", iccid, order_ref)
        return EsimInfo(
            found=True,
            provider=self.name,
            iccid=iccid or "8944100000000000000",
            status="not_installed",
            data_total_mb=10240,
            data_used_mb=0,
            data_remaining_mb=10240,
            expires_at="2026-11-30",
            qr_code="LPA:1$smdp.esimcard.example$MOCK-ACTIVATION",
            smdp_address="smdp.esimcard.example",
            activation_code="MOCK-ACTIVATION",
            raw={"stub": True},
        )
