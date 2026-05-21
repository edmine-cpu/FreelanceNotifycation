from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr
    telegram_chat_id: str
    freelancehunt_token: SecretStr

    skill_id: int = 180
    poll_interval: int = 60
    state_file: Path = Path("/data/state.json")
    send_existing_on_first_run: bool = False
    page_size: int = 5
    history_size: int = 50
    listing_url: str = "https://freelancehunt.com/projects/skill/razrabotka-botov/180.html"
    category_name: str = "Разработка ботов"

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = "gemini-2.5-flash"
    gemini_enabled: bool = True
    gemini_timeout_sec: float = 20.0

    @property
    def gemini_active(self) -> bool:
        return self.gemini_enabled and bool(self.gemini_api_key.get_secret_value())
