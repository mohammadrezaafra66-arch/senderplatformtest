"""توابع تولید کلید Redis برای Workerها."""


def queue_key(platform: str, account_id: int | str) -> str:
    return f"queue:{platform}:{account_id}"


def delay_key(account_id: int | str) -> str:
    return f"config:delay:{account_id}"


def hourly_rate_key(account_id: int | str, hour: str) -> str:
    return f"rate:{account_id}:{hour}"


def daily_rate_key(account_id: int | str, day: str) -> str:
    return f"rate:daily:{account_id}:{day}"


def daily_cap_alert_key(account_id: int | str, day: str) -> str:
    return f"rate:capalert:{account_id}:{day}"


def hourly_config_key(account_id: int | str) -> str:
    return f"config:hours:{account_id}"


def kill_switch_key() -> str:
    return "system:kill_switch"


def whatsapp_send_kill_switch_key() -> str:
    return "system:whatsapp_send_disabled"


def account_pause_key(account_id: int | str) -> str:
    return f"account:{account_id}:paused"


def campaign_pause_key(campaign_id: int | str) -> str:
    return f"campaign:{campaign_id}:paused"


def whatsapp_browser_lock_key(account_id: int | str) -> str:
    return f"lock:wa:browser:{account_id}"


def worker_heartbeat_key(platform: str, hostname: str) -> str:
    return f"worker:alive:{platform}:{hostname}"


def rubika_send_lock_key(account_id: int | str) -> str:
    return f"lock:rubika:send:{account_id}"


def rubika_reserve_key(account_id: int | str, token: str) -> str:
    return f"rubika:reserve:{account_id}:{token}"


def rubika_failure_count_key(account_id: int | str) -> str:
    return f"rubika:failcount:{account_id}"


def rubika_cooldown_meta_key(account_id: int | str) -> str:
    return f"rubika:cooldown:{account_id}"


def rubika_throttle_key(account_id: int | str) -> str:
    return f"rubika:throttle:{account_id}"
