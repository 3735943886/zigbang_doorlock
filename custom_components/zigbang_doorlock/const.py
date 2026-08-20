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

# 락별 상태캐시 키: device_id, tp_id, model, name, locked, battery_raw, rssi, pin_registry,
# last_access, last_method, last_user_name, last_event_at, last_pin_id

# IDPEVENT(622)의 access -> 대분류 라벨. RFC(외부 물리인증)는 실제로는 PIN_TYPE_LABELS 로
# 더 세분화됨(아래) — 이건 그 세분화가 안 될 때(레지스트리 미동기화 등)의 폴백.
ACCESS_LABELS: dict[str, str] = {
    "SVR": "remote",
    "RFC": "external",
    "INDOOR": "indoor",
    "AUTO": "auto_relock",
}

# access=="RFC" 인 IDPEVENT만 pinId -> Basic-AttrGroup 의 pinInfoXXX 레지스트리로 조회해서
# 세분화한다. SVR/AUTO/INDOOR 는 레지스트리에 값이 있어도 일부러 안 씀:
#   - SVR: pinId 가 원격열림용 임시NFC키(HA 자신의 injection 포함)를 가리켜서 pinType이 항상
#     NFC 로 나옴 — "remote"가 실제 의미에 더 맞음(누가 앱으로 열었나가 중요, 임시키 구현detail 아님).
#   - AUTO/INDOOR: pinId 가 항상 0(MST, 필러값) — 실제 자격증명이 아님(PROTOCOL.md §9-1, fixture 실측 둘 다 확인).
PIN_TYPE_LABELS: dict[str, str] = {
    "MST": "master",
    "RFC": "keytag",
    "FGP": "fingerprint",
    "NFC": "nfc_tag",
    "NUM": "keypad",
    "RMC": "remote_controller",
    "VCE": "voice",
    "BLE": "bluetooth",
    "FCE": "face",
    "SDK": "temp_key",  # TempKeyListViewModel(임시키 목록화면)에서만 쓰는 타입 — 추정
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
