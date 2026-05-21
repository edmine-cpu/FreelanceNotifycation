import asyncio

from app.app import run
from app.config import Settings
from app.logging_setup import configure_logging


def main() -> None:
    configure_logging()
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
