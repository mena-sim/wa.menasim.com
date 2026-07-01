from __future__ import annotations

from app.core.logging import get_logger
from app.services.providers.base import EsimInfo, EsimProvider

logger = get_logger(__name__)


class EsimAccessProvider(EsimProvider):
    """eSIMaccess API adapter.

    STUB: returns mock data until real credentials + endpoint mapping are supplied.
    """

    name = "esimaccess"

    def get_esim(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        if not self.api_key:
            return EsimInfo(
                found=False,
                provider=self.name,
                error="eSIMaccess provider not configured (missing API key).",
            )
        logger.info("[esimaccess] stub get_esim iccid=%s order_ref=%s", iccid, order_ref)
        return EsimInfo(
            found=True,
            provider=self.name,
            iccid=iccid or "8944200000000000000",
            status="active",
            data_total_mb=3072,
            data_used_mb=512,
            data_remaining_mb=2560,
            expires_at="2026-10-15",
            qr_code="LPA:1$smdp.esimaccess.example$MOCK-ACTIVATION",
            smdp_address="smdp.esimaccess.example",
            activation_code="MOCK-ACTIVATION",
            raw={"stub": True},
        )
