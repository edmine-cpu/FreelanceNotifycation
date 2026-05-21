from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Project:
    id: str
    url: str
    title: str
    budget: str
    description: str
    relative_time: str
    absolute_time: str
    published_ts: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            id=str(data["id"]),
            url=data.get("url", ""),
            title=data.get("title", ""),
            budget=data.get("budget", ""),
            description=data.get("description", ""),
            relative_time=data.get("relative_time", ""),
            absolute_time=data.get("absolute_time", ""),
            published_ts=int(data.get("published_ts", 0)),
        )
