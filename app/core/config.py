from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_name: str = "menasim WA support"
    database_url: str = "sqlite:///./data/app.db"
    chroma_dir: str = "./data/chroma"
    public_base_url: str = "http://127.0.0.1:8080"
    log_level: str = "INFO"

    # LLM (DeepSeek)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Agent guardrails
    agent_max_tool_iters: int = 6
    agent_max_tokens: int = 1024
    rate_limit_per_minute: int = 20

    # WooCommerce
    wc_base_url: str = ""
    wc_consumer_key: str = ""
    wc_consumer_secret: str = ""
    wc_esim_iccid_meta: str = "iccid,_iccid,esim_iccid"
    wc_esim_qr_meta: str = "qr,qr_code,esim_qr,activation_code"
    wc_esim_status_meta: str = "esim_status,status"

    # Telnyx WhatsApp
    telnyx_api_key: str = ""
    telnyx_whatsapp_from: str = ""
    telnyx_messaging_profile_id: str = ""
    telnyx_webhook_public_key: str = ""

    # SMTP escalation alerts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    alert_email_to: str = ""

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.telnyx_api_key and self.telnyx_whatsapp_from)

    @property
    def woocommerce_enabled(self) -> bool:
        return bool(self.wc_base_url and self.wc_consumer_key and self.wc_consumer_secret)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.alert_email_to)

    def csv(self, value: str) -> list[str]:
        return [part.strip() for part in (value or "").split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
