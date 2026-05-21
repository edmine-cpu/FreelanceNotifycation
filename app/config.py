import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: str
    poll_interval: int
    state_file: Path
    send_existing_on_first_run: bool
    page_size: int
    history_size: int
    listing_url: str
    category_name: str
    freelancehunt_token: str
    skill_id: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
            poll_interval=int(os.environ.get("POLL_INTERVAL", "60")),
            state_file=Path(os.environ.get("STATE_FILE", "/data/state.json")),
            send_existing_on_first_run=_bool("SEND_EXISTING_ON_FIRST_RUN", False),
            page_size=int(os.environ.get("PAGE_SIZE", "5")),
            history_size=int(os.environ.get("HISTORY_SIZE", "50")),
            listing_url=os.environ.get(
                "LISTING_URL",
                "https://freelancehunt.com/projects/skill/razrabotka-botov/180.html",
            ),
            category_name=os.environ.get("CATEGORY_NAME", "Разработка ботов"),
            freelancehunt_token=_required("FREELANCEHUNT_TOKEN"),
            skill_id=int(os.environ.get("SKILL_ID", "180")),
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is required")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
