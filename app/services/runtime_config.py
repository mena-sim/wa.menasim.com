from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt, encrypt
from app.models.app_setting import AppSetting

# key -> (group, is_secret, settings_attr_or_None, literal_default)
# If settings_attr is set, the .env/Settings value is the fallback default.
SCHEMA: dict[str, tuple[str, bool, str | None, Any]] = {
    # DeepSeek LLM
    "deepseek_api_key": ("deepseek", True, "deepseek_api_key", ""),
    "deepseek_base_url": ("deepseek", False, "deepseek_base_url", "https://api.deepseek.com"),
    "deepseek_model": ("deepseek", False, "deepseek_model", "deepseek-chat"),
    "agent_temperature": ("deepseek", False, None, "0.3"),
    "agent_max_tokens": ("deepseek", False, "agent_max_tokens", "1024"),
    # Telnyx
    "telnyx_api_key": ("telnyx", True, "telnyx_api_key", ""),
    "telnyx_messaging_profile_id": ("telnyx", False, "telnyx_messaging_profile_id", ""),
    "telnyx_webhook_public_key": ("telnyx", True, "telnyx_webhook_public_key", ""),
    # WhatsApp
    "whatsapp_provider": ("whatsapp", False, "whatsapp_provider", "telnyx"),
    "telnyx_whatsapp_from": ("whatsapp", False, "telnyx_whatsapp_from", ""),
    "whatsapp_display_name": ("whatsapp", False, None, "eSIM Support"),
    "whatsapp_business_id": ("whatsapp", False, None, ""),
    # Twilio WhatsApp + SMS
    "twilio_account_sid": ("twilio", False, "twilio_account_sid", ""),
    "twilio_auth_token": ("twilio", True, "twilio_auth_token", ""),
    "twilio_whatsapp_from": ("twilio", False, "twilio_whatsapp_from", ""),
    "twilio_sms_from": ("twilio", False, "twilio_sms_from", ""),
    "twilio_sms_enabled": ("twilio", False, "twilio_sms_enabled", "false"),
    # Meta WhatsApp Cloud API (direct)
    "meta_whatsapp_token": ("meta", True, "meta_whatsapp_token", ""),
    "meta_phone_number_id": ("meta", False, "meta_phone_number_id", ""),
    "meta_whatsapp_from": ("meta", False, "meta_whatsapp_from", ""),
    "meta_app_secret": ("meta", True, "meta_app_secret", ""),
    "meta_verify_token": ("meta", False, "meta_verify_token", ""),
    "meta_waba_id": ("meta", False, "meta_waba_id", ""),
    # WordPress / WooCommerce
    "wc_base_url": ("wordpress", False, "wc_base_url", ""),
    "wc_consumer_key": ("wordpress", True, "wc_consumer_key", ""),
    "wc_consumer_secret": ("wordpress", True, "wc_consumer_secret", ""),
    "wc_api_version": ("wordpress", False, None, "wc/v3"),
    "wc_esim_iccid_meta": (
        "wordpress",
        False,
        "wc_esim_iccid_meta",
        "order_iccid,airalo_data_sims_info_iccid,iccid,_iccid,esim_iccid",
    ),
    "wc_esim_qr_meta": (
        "wordpress",
        False,
        "wc_esim_qr_meta",
        "airalo_data_sims_info_qrcode,order_lpa,airalo_data_sims_info_qrcode_url,qr,qr_code,esim_qr,activation_code",
    ),
    "wc_esim_status_meta": (
        "wordpress",
        False,
        "wc_esim_status_meta",
        "airalo_data_sims_data_status,esim_status,status",
    ),
    "wc_sync_orders": ("wordpress", False, None, "true"),
    "wc_sync_kb": ("wordpress", False, None, "false"),
    # Agent & KB
    "agent_name": ("agent", False, None, "Aya"),
    "agent_tone": ("agent", False, None, "Friendly & concise"),
    "agent_system_instructions": ("agent", False, None, ""),
    "agent_max_tool_iters": ("agent", False, "agent_max_tool_iters", "6"),
    "rate_limit_per_minute": ("agent", False, "rate_limit_per_minute", "20"),
    # Voice notes (DeepInfra Whisper transcription)
    "deepinfra_api_key": ("voice", True, "deepinfra_api_key", ""),
    "deepinfra_whisper_model": ("voice", False, "deepinfra_whisper_model", "openai/whisper-large-v3"),
    "deepinfra_vision_model": (
        "voice",
        False,
        "deepinfra_vision_model",
        "meta-llama/Llama-3.2-11B-Vision-Instruct",
    ),
    "voice_enabled": ("voice", False, None, "true"),
    # SMTP alerts
    "smtp_host": ("smtp", False, "smtp_host", ""),
    "smtp_port": ("smtp", False, "smtp_port", "587"),
    "smtp_user": ("smtp", False, "smtp_user", ""),
    "smtp_pass": ("smtp", True, "smtp_pass", ""),
    "smtp_from": ("smtp", False, "smtp_from", ""),
    "alert_email_to": ("smtp", False, "alert_email_to", ""),
}

_SECRET_KEYS = {k for k, v in SCHEMA.items() if v[1]}


def _env_default(key: str) -> str:
    group, is_secret, attr, literal = SCHEMA[key]
    if attr:
        val = getattr(get_settings(), attr, None)
        if val is not None and str(val) != "":
            return str(val)
    return str(literal)


def get(db: Session, key: str) -> str:
    if key not in SCHEMA:
        return ""
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    if row is not None and row.value != "":
        return decrypt(row.value) if row.is_secret else row.value
    return _env_default(key)


def get_bool(db: Session, key: str) -> bool:
    return str(get(db, key)).strip().lower() in ("1", "true", "yes", "on", "enabled")


def get_int(db: Session, key: str, fallback: int = 0) -> int:
    try:
        return int(float(get(db, key)))
    except (ValueError, TypeError):
        return fallback


def get_float(db: Session, key: str, fallback: float = 0.0) -> float:
    try:
        return float(get(db, key))
    except (ValueError, TypeError):
        return fallback


def set_value(db: Session, key: str, value: str) -> None:
    if key not in SCHEMA:
        return
    is_secret = key in _SECRET_KEYS
    stored = encrypt(value) if (is_secret and value) else ("" if is_secret else value)
    row = db.execute(select(AppSetting).where(AppSetting.key == key)).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=key, value=stored, is_secret=is_secret)
        db.add(row)
    else:
        row.value = stored
        row.is_secret = is_secret


def set_group(db: Session, group: str, values: dict[str, Any]) -> None:
    for key, value in values.items():
        g = SCHEMA.get(key, (None,))[0]
        if g == group:
            # For secrets, ignore masked placeholder so we don't overwrite with mask.
            if key in _SECRET_KEYS and _is_mask(str(value)):
                continue
            set_value(db, key, "" if value is None else str(value))
    db.commit()


def _is_mask(value: str) -> bool:
    """True if the value is the masked placeholder (so we don't overwrite a real secret)."""
    v = (value or "").strip()
    if v == "":
        return True
    return set(v) <= {"•", "*"}


def group_view(db: Session, group: str) -> dict[str, Any]:
    """Return effective values for a group; secrets are masked but flagged as set."""
    out: dict[str, Any] = {}
    for key, (g, is_secret, _attr, _lit) in SCHEMA.items():
        if g != group:
            continue
        val = get(db, key)
        if is_secret:
            out[key] = "••••••••" if val else ""
            out[f"{key}__set"] = bool(val)
        else:
            out[key] = val
    return out


# ---- Typed effective-config accessors used by services ----

def llm_config(db: Session) -> dict[str, Any]:
    return {
        "api_key": get(db, "deepseek_api_key"),
        "base_url": get(db, "deepseek_base_url"),
        "model": get(db, "deepseek_model"),
        "temperature": get_float(db, "agent_temperature", 0.3),
        "max_tokens": get_int(db, "agent_max_tokens", 1024),
    }


def llm_enabled(db: Session) -> bool:
    return bool(get(db, "deepseek_api_key"))


def whatsapp_provider(db: Session) -> str:
    value = (get(db, "whatsapp_provider") or "telnyx").strip().lower()
    return value if value in ("telnyx", "twilio", "meta") else "telnyx"


def whatsapp_from_number(db: Session) -> str:
    provider = whatsapp_provider(db)
    if provider == "twilio":
        return get(db, "twilio_whatsapp_from")
    if provider == "meta":
        return get(db, "meta_whatsapp_from") or get(db, "telnyx_whatsapp_from")
    return get(db, "telnyx_whatsapp_from")


def whatsapp_enabled(db: Session) -> bool:
    provider = whatsapp_provider(db)
    if provider == "twilio":
        return bool(
            get(db, "twilio_account_sid")
            and get(db, "twilio_auth_token")
            and get(db, "twilio_whatsapp_from")
        )
    if provider == "meta":
        return bool(get(db, "meta_whatsapp_token") and get(db, "meta_phone_number_id"))
    return bool(get(db, "telnyx_api_key") and get(db, "telnyx_whatsapp_from"))


def woocommerce_enabled(db: Session) -> bool:
    return bool(
        get(db, "wc_base_url") and get(db, "wc_consumer_key") and get(db, "wc_consumer_secret")
    )


def smtp_enabled(db: Session) -> bool:
    return bool(get(db, "smtp_host") and get(db, "alert_email_to"))


def sms_from_number(db: Session) -> str:
    """Twilio SMS sender; falls back to the WhatsApp Twilio number if SMS is unset."""
    return get(db, "twilio_sms_from") or get(db, "twilio_whatsapp_from")


def sms_enabled(db: Session) -> bool:
    if not get_bool(db, "twilio_sms_enabled"):
        return False
    return bool(
        get(db, "twilio_account_sid")
        and get(db, "twilio_auth_token")
        and sms_from_number(db)
    )


def transcription_config(db: Session) -> dict[str, Any]:
    return {
        "api_key": get(db, "deepinfra_api_key"),
        "model": get(db, "deepinfra_whisper_model") or "openai/whisper-large-v3",
    }


def voice_enabled(db: Session) -> bool:
    return bool(get(db, "deepinfra_api_key")) and get_bool(db, "voice_enabled")
