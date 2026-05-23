import re
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.projects import Category

LISTING_URL_TEMPLATE = "https://freelancehunt.com/projects?skills[]={skill_id}"

# Human-readable names for known skills. Unknown IDs fall back to
# "Категория #<id>", so adding a category is just a new ID in SKILL_IDS.
SKILL_NAMES: dict[int, str] = {
    99: "Веб-программирование",
    180: "Разработка ботов",
}


def parse_skill_ids(raw: str) -> list[int]:
    """Parse a comma/space/semicolon-separated list of skill IDs, preserving
    order and dropping duplicates. Raises ValueError on a non-integer token."""
    result: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[,;\s]+", raw.strip()):
        if not token:
            continue
        try:
            skill_id = int(token)
        except ValueError:
            raise ValueError(
                f"invalid SKILL_IDS entry {token!r}: expected integers like '99,180'"
            ) from None
        if skill_id not in seen:
            seen.add(skill_id)
            result.append(skill_id)
    return result


def build_category(skill_id: int) -> Category:
    return Category(
        skill_id=skill_id,
        name=SKILL_NAMES.get(skill_id, f"Категория #{skill_id}"),
        listing_url=LISTING_URL_TEMPLATE.format(skill_id=skill_id),
    )


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

    # Comma-separated list of FreelanceHunt skill IDs to watch, e.g. "99,180".
    # Kept as a string (not list[int]) to avoid pydantic-settings JSON-decoding
    # of complex env values; parsed into categories below.
    skill_ids: str = "180"
    poll_interval: int = 60
    state_file: Path = Path("/data/state.json")
    send_existing_on_first_run: bool = False
    page_size: int = 5
    history_size: int = 50

    gemini_api_key: SecretStr = Field(default=SecretStr(""))
    gemini_model: str = "gemini-2.5-flash"
    gemini_enabled: bool = True
    gemini_timeout_sec: float = 20.0

    @field_validator("skill_ids")
    @classmethod
    def _check_skill_ids(cls, value: str) -> str:
        if not parse_skill_ids(value):
            raise ValueError("SKILL_IDS must contain at least one integer skill ID, e.g. '99,180'")
        return value

    @property
    def categories(self) -> list[Category]:
        return [build_category(skill_id) for skill_id in parse_skill_ids(self.skill_ids)]

    @property
    def category_label(self) -> str:
        return ", ".join(category.name for category in self.categories)

    @property
    def gemini_active(self) -> bool:
        return self.gemini_enabled and bool(self.gemini_api_key.get_secret_value())
