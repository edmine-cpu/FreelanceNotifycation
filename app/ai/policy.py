"""AI access is an application policy, never a preference or a chat setting."""

AI_OWNER_ID = 992784212
ACCESS_DENIED = "AI доступен только владельцу бота."


def ai_allowed(user_id: int | None) -> bool:
    return type(user_id) is int and user_id == AI_OWNER_ID


def require_ai_access(user_id: int | None) -> None:
    if not ai_allowed(user_id):
        raise PermissionError(ACCESS_DENIED)
