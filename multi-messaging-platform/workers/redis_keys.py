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


def rubika_quarantine_key(account_id: int | str) -> str:
    return f"rubika:quarantine:{account_id}"


def rubika_health_success_key(account_id: int | str, window: str) -> str:
    return f"rubika:health:ok:{account_id}:{window}"


def rubika_health_fail_key(account_id: int | str, window: str) -> str:
    return f"rubika:health:fail:{account_id}:{window}"


def rubika_health_consec_key(account_id: int | str) -> str:
    return f"rubika:health:consec:{account_id}"


def rubika_health_meta_key(account_id: int | str) -> str:
    return f"rubika:health:meta:{account_id}"


def rubika_circuit_state_key() -> str:
    return "rubika:circuit:state"


def rubika_circuit_meta_key() -> str:
    return "rubika:circuit:meta"


def rubika_circuit_probe_key() -> str:
    return "rubika:circuit:probe"


def rubika_circuit_sys_accounts_key(window: str) -> str:
    return f"rubika:circuit:sysacct:{window}"


def rubika_circuit_sys_count_key(window: str) -> str:
    return f"rubika:circuit:syscount:{window}"


def rubika_incident_key(dedupe: str) -> str:
    return f"rubika:incident:{dedupe}"


def rubika_incident_index_key() -> str:
    return "rubika:incidents:open"


def rubika_alert_key(dedupe: str) -> str:
    return f"rubika:alert:{dedupe}"


def rubika_alert_index_key() -> str:
    return "rubika:alerts:open"


def rubika_inflight_count_key(account_id: int | str) -> str:
    return f"rubika:inflight:count:{account_id}"


def rubika_inflight_member_key(account_id: int | str, message_id: int | str) -> str:
    return f"rubika:inflight:msg:{account_id}:{message_id}"


def rubika_send_lease_key(message_id: int | str) -> str:
    return f"rubika:send:lease:{message_id}"


def rubika_delayed_retry_key() -> str:
    return "rubika:retry:delayed"


def campaign_safety_pause_key(campaign_id: int | str) -> str:
    return f"campaign:{campaign_id}:safety_pause"


def campaign_dispatch_fairness_key() -> str:
    return "campaign:dispatch:fairness:cursor"


def campaign_account_skip_key(account_id: int | str) -> str:
    return f"campaign:dispatch:skip_account:{account_id}"
