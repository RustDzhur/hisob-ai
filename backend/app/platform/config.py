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

    # Хранилище файлов (любое S3-совместимое; в compose — SeaweedFS).
    s3_endpoint_url: str = "http://storage:8333"
    s3_bucket: str = "hisob-documents"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    max_upload_bytes: int = 20 * 1024 * 1024

    # Фоновые задачи (Dramatiq + Redis). Без REDIS_URL (тесты) используется заглушка брокера.
    redis_url: str | None = None

    # Распознавание документов. Ключи есть только у воркера; mock — только для тестов и помечается в UI.
    ai_provider: str = "openai"  # openai | deepseek | mock
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com"
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    ai_timeout_seconds: int = 120
    ai_max_jobs_per_day: int = 200  # защита от случайного расхода на организацию
    # Доверять X-Forwarded-For только если перед API стоит наш прокси.
    trust_proxy_headers: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
