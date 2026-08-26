"""Zigbang Doorlock 상수."""
from __future__ import annotations

DOMAIN = "zigbang_doorlock"
PLATFORMS = ["lock", "sensor", "event", "button", "switch"]

MANUFACTURER = "Zigbang (Samsung SDS IHP)"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_IMEI = "imei"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_LOCKS = "locks"
# relay observer(9883)의 선택적 basic auth(zigbang addon 0.1.5+ observer_user/observer_pass) —
# 직방 계정 CONF_USERNAME/CONF_PASSWORD와는 별개 자격증명이라 이름을 분리한다.
CONF_RELAY_USERNAME = "relay_username"
CONF_RELAY_PASSWORD = "relay_password"

DEFAULT_RELAY_PORT = 9883

# HA 전용 영구 unlock pin에 항상 붙이는 이름(build_register_key 기본값과 동일). 저장된 토큰이
# entry.data와 안 맞아도 이 이름의 pin이 락에 이미 있으면 그 토큰을 채택해서 재사용한다 —
# 안 그러면 재시작마다 새 토큰을 만들어서 매번 새 pin이 쌓인다(실측 확인된 버그, 2026-08-20).
# "HA"처럼 짧은 이름은 클라우드가 자동 발급하는 다른 종류의 키(예: 새 기기 로그인 시
# 자동 프로비저닝되는 편의키)와 이름이 겹칠 위험이 있고, 이름으로 채택(adopt)하는 로직이
# 그 중 아무거나 골라버리면 실제 동작하는 키 대신 엉뚱한 걸 채택해 unlock이 조용히 깨질 수
# 있다(실측 확인, 2026-08-21). 그래서 우연히 겹칠 가능성이 낮은 이름을 쓴다.
HA_PIN_NAME = "ZBDL-HA-KEY"

# 락별 상태캐시 키: device_id, tp_id, model, name, locked, battery_raw, rssi, pin_registry,
# last_access, last_method, last_user_name, last_pin_id, last_unlock_at,
# dummy_mode, use_magic_number, use_2way_auth, away_indoor_armed(switch.py 참조),
# jammed(lock.py의 is_jammed -> HA STATE_JAMMED 참조)

# IDPEVENT(622)의 access -> 대분류 라벨. 카드/지문/마스터코드 등 실제 자격증명으로 열린 경우엔
# access 값 자체가 아래 PIN_TYPE_LABELS 코드와 동일하게 온다(실측 2026-08-20 relay tap 확인:
# RFC=카드/키태그, FGP=지문, MST=마스터/번호코드). __init__.py는 이걸 하드코딩된 access 허용목록이
# 아니라 "레지스트리[pinId].pinType == access" 매칭으로 판정한다 — 그래서 여기 없는 새 access
# 코드가 나와도(아는 pinType이기만 하면) 코드 수정 없이 자동으로 세분화된다. 이 딕셔너리는 매칭이
# 안 되는 경우(AUTO/INDOOR/MNU/RMC, 레지스트리 미동기화, 진짜 미상 코드 등)의 폴백 라벨이고,
# 없는 키는 access 원본 문자열 그대로 노출한다(ACCESS_LABELS.get(access, access)) — 미상 값도
# last_method/event.access attribute에 그대로 남아서 안 씹힌다.
#
# SVR은 위 매칭에서 예외 처리한다(__init__.py 참조) — pinType과 같을 수 없는 값(원격 트리거
# 채널을 뜻함)인데도 pinId/pin은 실제 등록된 키를 정확히 가리키는 게 실측 확인됨(2026-08-21).
# 그래서 method는 "remote"로 두되 pin_name은 레지스트리에서 채운다 — 이걸 안 하면 HA 자신이
# 트리거한 unlock(사실상 이 통합구성요소로 여는 모든 경우)이 전부 이름 unknown으로 떨어진다.
ACCESS_LABELS: dict[str, str] = {
    "SVR": "remote",
    "INDOOR": "indoor",
    "AUTO": "auto_relock",
    "MNU": "manual_lock",  # 수동 잠금(안에서 손으로) — 실측 2026-08-20 확인, INDOOR(수동 열림)의 짝
    "RMC": "remote_controller",  # 리모컨 — pinId 항상 0(필러)이라 레지스트리 조회 무의미(실측 확인)
}

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

# IDPEVENT msgCategory 622(잠금상태변경)의 access -> event_type. RFC/FGP/MST(카드/지문/마스터코드)는
# 전부 "외부 물리 자격증명으로 열림"이라는 같은 대분류로 묶는다 — 세부 방법은 event의 pin_type/
# pin_name attribute가 담당(실측 2026-08-20 확인).
EVENT_TYPE_UNLOCKED = {
    "SVR": "unlocked_remote",
    "RFC": "unlocked_external",
    "FGP": "unlocked_external",
    "MST": "unlocked_external",
    "INDOOR": "unlocked_indoor",
}
EVENT_TYPE_LOCKED = "locked"
EVENT_TYPE_KEY_ADDED = "key_added"
EVENT_TYPE_KEY_REMOVED = "key_removed"
# IDPEVENT msgCategory 648/652 — 클라우드 앱 로그 실측(2026-08-26, fixtures/재택안심로컬과잼.capture):
# 648은 문열림 유지 32초 후 발생("문이 30초 이상 열려 있습니다" 임계값과 정확히 일치),
# 652는 재잠금 시도(재밍 재현) 중 문열림 15초 만에 발생("문이 제대로 닫히지 않았습니다") —
# 둘 다 부가데이터가 없어(access/pinId 등) locked/key 이벤트처럼 상태갱신은 안 하고 활동기록만 남긴다.
EVENT_TYPE_DOOR_OPEN_TOO_LONG = "door_open_too_long"
EVENT_TYPE_DOOR_NOT_CLOSED = "door_not_closed"

EVENT_TYPES = [
    EVENT_TYPE_LOCKED,
    "unlocked_remote",
    "unlocked_external",
    "unlocked_indoor",
    "unlocked",  # access 미상 폴백
    EVENT_TYPE_KEY_ADDED,
    EVENT_TYPE_KEY_REMOVED,
    EVENT_TYPE_DOOR_OPEN_TOO_LONG,
    EVENT_TYPE_DOOR_NOT_CLOSED,
]

SIGNAL_EVENT = f"{DOMAIN}_event_{{}}_{{}}"  # .format(entry_id, tp_id)
