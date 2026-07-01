from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.provider_setting import ProviderSetting
from app.services.providers.airalo import AiraloProvider
from app.services.providers.base import EsimProvider
from app.services.providers.esimaccess import EsimAccessProvider
from app.services.providers.esimcard import EsimCardProvider

logger = get_logger(__name__)

# name -> adapter class. New providers: add a class + register here + create a settings row.
_ADAPTERS: dict[str, type[EsimProvider]] = {
    "airalo": AiraloProvider,
    "esimcard": EsimCardProvider,
    "esimaccess": EsimAccessProvider,
}


def available_provider_names() -> list[str]:
    return sorted(_ADAPTERS.keys())


def _build(setting: ProviderSetting) -> EsimProvider | None:
    cls = _ADAPTERS.get(setting.name)
    if not cls:
        logger.warning("No adapter registered for provider '%s'", setting.name)
        return None
    extra = {}
    if setting.extra_json:
        try:
            extra = json.loads(setting.extra_json)
        except json.JSONDecodeError:
            extra = {}
    return cls(
        base_url=setting.base_url,
        api_key=setting.api_key,
        api_secret=setting.api_secret,
        extra=extra,
    )


def get_provider(db: Session, name: str | None = None) -> EsimProvider | None:
    """Resolve a provider: named one if given, else the default enabled provider."""
    stmt = select(ProviderSetting).where(ProviderSetting.enabled.is_(True))
    if name:
        stmt = select(ProviderSetting).where(ProviderSetting.name == name)
    settings = db.execute(stmt).scalars().all()
    if not settings:
        return None
    chosen = next((s for s in settings if s.is_default), settings[0])
    return _build(chosen)


def enabled_providers(db: Session) -> list[EsimProvider]:
    settings = (
        db.execute(select(ProviderSetting).where(ProviderSetting.enabled.is_(True)))
        .scalars()
        .all()
    )
    out: list[EsimProvider] = []
    for s in settings:
        provider = _build(s)
        if provider:
            out.append(provider)
    return out
