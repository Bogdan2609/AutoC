from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://autoparts:autoparts_local@localhost:5432/autoparts"
    admin_api_token: str = ""
    admin_legacy_token_enabled: bool = True  # disable explicitly after staff owner login succeeds
    browser_csrf_secret: str = ""
    public_base_url: str = "http://localhost:8000"
    offer_max_age_seconds: int = Field(default=900, ge=30, le=604800)
    app_env: str = "development"
    redis_url: str = "redis://localhost:6379/0"
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_fake_enabled: bool = False  # test-only opt-in, never production
    payments_reconciliation_enabled: bool = False  # explicit opt-in after sandbox checks
    yookassa_vat_code: int = Field(default=0, ge=0, le=6)
    supplier_api_enabled: bool = False
    supplier_procurement_enabled: bool = False
    # Real checkout is always explicit, never enabled by a code deployment.
    real_supplier_checkout_enabled: bool = False
    marketplace_demo_mode: bool = False
    discovery_ip_per_minute: int = Field(default=6, ge=1, le=200)
    discovery_ip_per_hour: int = Field(default=30, ge=1, le=1000)
    discovery_global_per_minute: int = Field(default=90, ge=1, le=10000)
    discovery_max_pending_jobs: int = Field(default=200, ge=4, le=100000)
    supplier_requests_per_hour: int = Field(default=60, ge=1, le=10000)
    partkom_login: str = ""
    partkom_password: str = ""
    adeo_login: str = ""
    adeo_password: str = ""
    forum_login: str = ""
    forum_password: str = ""
    rossko_key1: str = ""
    rossko_key2: str = ""
    rossko_delivery_id: str = ""
    rossko_address_id: str = ""


@lru_cache

def get_settings() -> Settings:
    return Settings()
