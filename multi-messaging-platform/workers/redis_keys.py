"""توابع تولید کلید Redis برای Workerها."""


def queue_key(platform: str, account_id: int | str) -> str:
    return f"queue:{platform}:{account_id}"


def delay_key(account_id: int | str) -> str:
    return f"config:delay:{account_id}"


def hourly_rate_key(account_id: int | str, hour: str) -> str:
    return f"rate:{account_id}:{hour}"


def hourly_config_key(account_id: int | str) -> str:
    return f"config:hours:{account_id}"


def kill_switch_key() -> str:
    return "system:kill_switch"


def account_pause_key(account_id: int | str) -> str:
    return f"account:{account_id}:paused"


def campaign_pause_key(campaign_id: int | str) -> str:
    return f"campaign:{campaign_id}:paused"
