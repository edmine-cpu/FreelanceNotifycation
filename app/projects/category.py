from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """A FreelanceHunt skill category the bot watches."""

    skill_id: int
    name: str
    listing_url: str
