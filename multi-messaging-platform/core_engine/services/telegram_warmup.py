"""Telegram warmup utilities for MTProto scheduling."""


def calculate_daily_cap(day: int) -> int:
    """Return the daily send cap based on warmup day."""
    if day <= 0:
        return 10
    if day >= 14:
        return 80
    return 10 + int((day / 14) * (80 - 10))
