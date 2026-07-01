from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.provider_setting import ProviderSetting
from app.schemas.settings import ProviderSettingIn, ProviderSettingOut
from app.services.providers import registry

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _to_out(s: ProviderSetting) -> ProviderSettingOut:
    return ProviderSettingOut(
        name=s.name,
        enabled=s.enabled,
        is_default=s.is_default,
        base_url=s.base_url,
        has_api_key=bool(s.api_key),
        has_api_secret=bool(s.api_secret),
    )


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(ProviderSetting)).scalars().all()
    return {
        "available_adapters": registry.available_provider_names(),
        "configured": [_to_out(r).model_dump() for r in rows],
    }


@router.put("/providers")
def upsert_provider(payload: ProviderSettingIn, db: Session = Depends(get_db)) -> dict:
    if payload.name not in registry.available_provider_names():
        return {
            "ok": False,
            "message": f"Unknown provider '{payload.name}'. Available: "
            f"{registry.available_provider_names()}",
        }

    row = db.execute(
        select(ProviderSetting).where(ProviderSetting.name == payload.name)
    ).scalar_one_or_none()
    if row is None:
        row = ProviderSetting(name=payload.name)
        db.add(row)

    row.enabled = payload.enabled
    row.is_default = payload.is_default
    row.base_url = payload.base_url
    if payload.api_key is not None:
        row.api_key = payload.api_key
    if payload.api_secret is not None:
        row.api_secret = payload.api_secret
    row.extra_json = payload.extra_json

    # Only one default at a time.
    if payload.is_default:
        for other in db.execute(
            select(ProviderSetting).where(ProviderSetting.name != payload.name)
        ).scalars():
            other.is_default = False

    db.commit()
    db.refresh(row)
    return {"ok": True, "provider": _to_out(row).model_dump()}
