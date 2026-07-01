from __future__ import annotations

from pydantic import BaseModel


class ProviderSettingIn(BaseModel):
    name: str
    enabled: bool = False
    is_default: bool = False
    base_url: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    extra_json: str | None = None


class ProviderSettingOut(BaseModel):
    name: str
    enabled: bool
    is_default: bool
    base_url: str | None = None
    has_api_key: bool = False
    has_api_secret: bool = False
