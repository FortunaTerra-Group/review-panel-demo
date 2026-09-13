def current_window(now: float, window_seconds: int) -> int:
    """Fixed window id: every request inside the same window_seconds bucket shares a key."""
    return int(now // window_seconds)


def window_key(tenant_id: str, now: float, window_seconds: int) -> str:
    return f"ratelimit:{tenant_id}:{current_window(now, window_seconds)}"
