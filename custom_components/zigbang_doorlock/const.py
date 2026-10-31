"""Zigbang Doorlock 상수."""
from __future__ import annotations

DOMAIN = "zigbang_doorlock"
PLATFORMS = ["lock", "sensor", "binary_sensor", "event", "button", "switch"]

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

# relay 실행 방식: embedded(HA 가 subprocess 로 직접 띄움, embedded_relay.py) | external(따로 띄운
# Docker/App 의 observer host:port 에 접속). 이 키가 없는 예전 entry 는 external 로 취급.
CONF_RELAY_MODE = "relay_mode"
RELAY_MODE_EMBEDDED = "embedded"
RELAY_MODE_EXTERNAL = "external"
CONF_CERT_NAME = "cert_name"
CONF_CERT_FILE = "cert_file"
CONF_CERT_KEY = "cert_key"
# 인증서 기본경로: HA 설정폴더 아래 zigbang_certs/(모든 설치방식에서 HA 가 보는 위치 — Docker 로 띄운
# HA 엔 /config 만 마운트돼있는 경우가 많음). 단 HAOS 표준 공유경로(/ssl, 커뮤니티 Let's Encrypt App 이
# 채우고 갱신까지 해줌)에 둘 다 이미 있으면 그쪽을 기본값으로 제안(config_flow._default_cert_paths).
CERT_DIR = "zigbang_certs"
CERT_FILE_NAME = "fullchain.pem"
CERT_KEY_NAME = "privkey.pem"
HAOS_CERT_FILE = "/ssl/fullchain.pem"
HAOS_CERT_KEY = "/ssl/privkey.pem"

# 내장 relay 고급설정(config flow 의 접힌 "advanced" 섹션). listen 포트는 provision 시 락에 주입한
# 값(RELAY_SETUP.md 7단계, 기본 18883/5683)과 같아야 함. 이 키들이 없는 entry 는 기본값으로 동작.
SECTION_ADVANCED = "advanced"
CONF_MQTTS_PORT = "mqtts_port"
CONF_UDP_PORT = "udp_port"
CONF_ROUTE_MODE = "route_mode"
DEFAULT_MQTTS_PORT = 18883
DEFAULT_UDP_PORT = 5683
# shadow(기본): 통과+관찰 + ack 선응답/업스트림(직방 클라우드) 장애시 로컬 대신응답(패키지 기본 rules 사용)
# — 클라우드가 죽어도 락/HA 가 계속 동작. proxy: 순수 통과+관찰만.
# decloud(업스트림 없는 완전 로컬)는 통합 쪽 동작 검증 전이라 아직 노출 안 함.
ROUTE_MODE_PROXY = "proxy"
ROUTE_MODE_SHADOW = "shadow"
ROUTE_MODES = [ROUTE_MODE_SHADOW, ROUTE_MODE_PROXY]
DEFAULT_ROUTE_MODE = ROUTE_MODE_SHADOW

# 락이 relay 에서 끊긴 뒤 이 시간(초)이 지나도 안 돌아오면 엔티티를 unavailable 로. relay 는 클라우드 연결이
# 리셋될 때마다 세션을 일부러 끊고 락이 2~3초 안에 재접속하므로(../zigbang session.rs 복구 폴링) 그 정도
# 깜빡임은 무시. "relay 연결" 센서(binary_sensor.py)는 유예 없이 즉시 반영.
RELAY_OFFLINE_GRACE = 30

# 내장 relay: PyPI 패키지 버전 = relay 바이너리 버전. upstream 은 docker/relay.toml.example 과 동일.
RELAY_REQUIREMENT = "zigbang-relay==0.1.8"
UPSTREAM_MQTTS = "mqtttcp.homeiot.zigbang-iot.com:8883"
UPSTREAM_UDP = "mqttudp.homeiot.zigbang-iot.com:5683"

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
