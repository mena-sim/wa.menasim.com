from __future__ import annotations

from app.core.logging import get_logger
from app.services.providers.base import EsimInfo, EsimProvider

logger = get_logger(__name__)


class AiraloProvider(EsimProvider):
    """Airalo Partner API adapter.

    STUB: returns mock data until real credentials + endpoint mapping are supplied.
    Wire real calls (OAuth token -> /v2/sims/{iccid}/... ) once keys are available.
    """

    name = "airalo"

    def get_esim(self, *, iccid: str | None = None, order_ref: str | None = None) -> EsimInfo:
        if not self.api_key:
            return EsimInfo(
                found=False,
                provider=self.name,
                error="Airalo provider not configured (missing API key).",
            )
        logger.info("[airalo] stub get_esim iccid=%s order_ref=%s", iccid, order_ref)
        return EsimInfo(
            found=True,
            provider=self.name,
            iccid=iccid or "8944000000000000000",
            status="active",
            data_total_mb=5120,
            data_used_mb=1280,
            data_remaining_mb=3840,
            expires_at="2026-12-31",
            qr_code="LPA:1$smdp.airalo.example$MOCK-ACTIVATION",
            smdp_address="smdp.airalo.example",
            activation_code="MOCK-ACTIVATION",
            raw={"stub": True},
        )
