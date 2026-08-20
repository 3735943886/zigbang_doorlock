"""Zigbang Doorlock 상수."""
from __future__ import annotations

DOMAIN = "zigbang_doorlock"
PLATFORMS = ["lock", "sensor", "event"]

MANUFACTURER = "Zigbang (Samsung SDS IHP)"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_IMEI = "imei"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_LOCKS = "locks"

DEFAULT_RELAY_PORT = 9883

# 락별 상태캐시 키: device_id, tp_id, model, name, locked, battery_raw, rssi,
# last_access, last_method, last_event_at, last_pin_id

# IDPEVENT data.access -> 사람이 읽는 라벨. RFC/INDOOR 등은 relay observer(IDPEVENT)만으로는
# 지문/카드/키패드 등 세부수단을 구분 못 함(그건 TLS 크기지문표 영역, 미구현) — 대분류만 노출.
ACCESS_LABELS: dict[str, str] = {
    "SVR": "remote",
    "RFC": "external",
    "INDOOR": "indoor",
    "AUTO": "auto_relock",
}

# IDPEVENT msgCategory 622(잠금상태변경)의 access -> event_type
EVENT_TYPE_UNLOCKED = {
    "SVR": "unlocked_remote",
    "RFC": "unlocked_external",
    "INDOOR": "unlocked_indoor",
}
EVENT_TYPE_LOCKED = "locked"
EVENT_TYPE_KEY_ADDED = "key_added"
EVENT_TYPE_KEY_REMOVED = "key_removed"

EVENT_TYPES = [
    EVENT_TYPE_LOCKED,
    "unlocked_remote",
    "unlocked_external",
    "unlocked_indoor",
    "unlocked",  # access 미상 폴백
    EVENT_TYPE_KEY_ADDED,
    EVENT_TYPE_KEY_REMOVED,
]

SIGNAL_EVENT = f"{DOMAIN}_event_{{}}_{{}}"  # .format(entry_id, tp_id)
