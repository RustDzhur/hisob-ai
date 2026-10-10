from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения. Секреты здесь не логируются."""

    model_config = SettingsConfigDict(extra="ignore")

    env: str = "dev"
    # Роль приложения (hisob_app): без BYPASSRLS, не владелец таблиц.
    database_url: str
    # Владелец схемы: только для Alembic.
    migration_database_url: str | None = None

    cookie_secure: bool = True
    session_ttl_hours: int = 24 * 14

    login_max_failures: int = 5
    login_window_minutes: int = 15
    password_min_length: int = 10
    invitation_ttl_days: int = 7
    # Доверять X-Forwarded-For только если перед API стоит наш прокси.
    trust_proxy_headers: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
