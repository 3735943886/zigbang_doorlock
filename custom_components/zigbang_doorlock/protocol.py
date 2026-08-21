"""직방 도어락 OCP 프로토콜 파싱/생성.

근거: ../zigbang(private/PROTOCOL.md §9~10, private/fixtures/r5c_publishes.jsonl 실측).
relay observer(9883)에서 tap 되는 메시지는 전부 평문 JSON(encType:"0") — 별도 복호 불필요.
"""
from __future__ import annotations

import json
import secrets
import time
import uuid
from datetime import datetime
from typing import Any

from .const import (
    EVENT_TYPE_KEY_ADDED,
    EVENT_TYPE_KEY_REMOVED,
    EVENT_TYPE_LOCKED,
    EVENT_TYPE_UNLOCKED,
    HA_PIN_NAME,
)

# Basic-AttrGroup(funcType 021) 페이로드는 "변경된 필드만" 부분갱신으로 오기도 함
# (fixture 실측 — 매번 전체 덤프가 아님) → 아는 키만 뽑아 상태캐시에 merge.
_ATTR_FIELD_MAP = {
    "battery": "battery_raw",
    "locked": "locked",
    "wifiStrength": "rssi",
}

# msgCategory: 622=잠금상태변경, 626=키등록확인, 628=키삭제확인, 634=키싱크(1회성),
# 670=DST정보(무관), 905=원인미상(무관) — PROTOCOL.md §10.
_RELEVANT_CATEGORIES = {622, 626, 628}


def parse_ocp_payload(raw: bytes) -> dict[str, Any] | None:
    """PUBLISH payload(bytes) -> OCP JSON dict. 실패시 None(락 하트비트 등 비JSON 프레임 존재 가능)."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def extract_basic_attrgroup_patch(data: dict[str, Any]) -> dict[str, Any]:
    """Basic-AttrGroup data에서 우리가 다루는 필드만 골라 상태패치로 변환."""
    return {
        dst: data[src]
        for src, dst in _ATTR_FIELD_MAP.items()
        if src in data
    }


def extract_pin_registry_patch(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Basic-AttrGroup의 pinInfoXXX 필드들 -> {pinId: {pin_type, pin_name}}.

    공식앱이 IDPEVENT(622)의 pinId로 "무엇으로 열었는지" 표시이름을 찾을 때 쓰는 것과 같은
    레지스트리(PROTOCOL.md §9-1/§10, fixture 실측) — 부분갱신으로 오므로 호출측에서 누적merge 필요.
    """
    patch: dict[int, dict[str, Any]] = {}
    for key, value in data.items():
        if key.startswith("pinInfo") and isinstance(value, dict) and "pinId" in value:
            patch[value["pinId"]] = {
                "pin_type": value.get("pinType"),
                "pin_name": value.get("pinName") or None,
            }
    return patch


def extract_idpevent(data: dict[str, Any], msg_date_ms: int | None) -> dict[str, Any] | None:
    """IDPEVENT data -> 정규화된 이벤트. 관심없는 category(634/670/905 등)는 None."""
    category = data.get("msgCategory")
    if category not in _RELEVANT_CATEGORIES:
        return None

    at = _epoch_ms_to_iso(msg_date_ms) if msg_date_ms else None

    if category == 622:
        locked = bool(data.get("locked"))
        access = data.get("access")
        if locked:
            event_type = EVENT_TYPE_LOCKED
        else:
            event_type = EVENT_TYPE_UNLOCKED.get(access, "unlocked")
        return {
            "event_type": event_type,
            "locked": locked,
            "access": access,
            "pin_id": data.get("pinId"),
            "at": at,
        }

    if category == 626:
        return {"event_type": EVENT_TYPE_KEY_ADDED, "pin_id": data.get("pinId"), "pin_type": data.get("pinType"), "at": at}

    if category == 628:
        return {"event_type": EVENT_TYPE_KEY_REMOVED, "pin_id": data.get("pinId"), "pin_type": data.get("pinType"), "at": at}

    return None


def battery_raw_to_pct(raw: int | None) -> int | None:
    """도어락 배터리 raw값 -> 표시용 %.

    임계값은 zigbang_doorlock_pyscript(zb_battery)와 동일 — 실측 기반 근사치이며
    선형 변환이 아니라 계단식(펌웨어가 보고하는 값 자체가 4~5단계 코드에 가까움)."""
    if raw is None:
        return None
    for threshold, pct in ((60, 100), (55, 80), (52, 60), (50, 40)):
        if raw >= threshold:
            return pct
    return 20


def build_register_key(sid: str, tpid: str, pin_token: str, pin_name: str = HA_PIN_NAME) -> tuple[str, dict[str, Any]]:
    """HA 전용 영구 NFC 키 등록(func 408). 딱 1회만 호출 — pin_token 은 이후 매 unlock 에서 재사용.

    같은 pin_token 으로 재등록하는 건 PROTOCOL.md §9 가 "미검증"이라고 경고한 영역이라, 호출측
    (relay_client.py)에서 "아직 등록 안 됐다고 확인된 경우"에만 불러야 한다.
    """
    topic = f"ocp/{sid}/{tpid}"
    start = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "version": "1.0",
        "msgType": "Q",
        "funcType": "030",
        "sId": sid,
        "tpId": tpid,
        "tId": tpid,
        "msgCode": "MSGBA0300001",
        "msgId": str(uuid.uuid4()),
        "msgDate": _now_ms(),
        "resCode": "",
        "resMsg": "",
        "dataFormat": "application/json",
        "severity": "0",
        "encType": "0",
        "authToken": "",
        "data": {
            "func": 408,
            "arg": {
                "pinType": "NFC",
                "pinSort": "PMT",
                "pinName": pin_name,
                "pin": pin_token,
                "pinStatus": "NOR",
                "start": start,
                "end": "2999-12-31 00:00",
                "dailyRepeat": {"start": "", "end": ""},
                "weeklyRepeat": [],
                "count": 0,
            },
            "from": pin_token,
        },
    }
    return topic, payload


def build_delete_key(sid: str, tpid: str, pin_token: str) -> tuple[str, dict[str, Any]]:
    """등록된 pin 삭제(func 420). helper/manage_pins.py의 cmd_delete와 동일한 실캡처 기준 payload
    (authToken 필드 자체 없음, resCode:"200") — button.py의 "HA 키 초기화" 버튼 전용, 위험도가
    높아서(잘못 지정하면 엉뚱한 키가 지워짐) 호출측에서 pin_token을 정확히 골라 넘겨야 한다."""
    topic = f"ocp/{sid}/{tpid}"
    payload = {
        "version": "1.0",
        "msgType": "Q",
        "funcType": "030",
        "sId": sid,
        "tpId": tpid,
        "tId": tpid,
        "msgCode": "MSGBA0300001",
        "msgId": str(uuid.uuid4()),
        "msgDate": _now_ms(),
        "resCode": "200",
        "resMsg": "",
        "dataFormat": "application/json",
        "severity": "0",
        "encType": "0",
        "data": {"func": 420, "arg": {"pin": pin_token}},
    }
    return topic, payload


def build_wake(sid: str, tpid: str) -> tuple[str, dict[str, Any]]:
    """func 508(arg 없음) — 실캡처(fixtures/r5c_publishes.jsonl #47-50/#65-68, 2026-08-20 실기기
    앱 unlock 실측)에서 트리거(407) 직전에 항상 먼저 오는 메시지. 이거 없이 407만 단독으로 보내면
    락이 그냥 무시함(실측: 물리적으로 안 열림) — 407 보내기 전에 항상 먼저 보내야 한다."""
    topic = f"ocp/{sid}/{tpid}"
    payload = {
        "version": "1.0",
        "msgType": "Q",
        "funcType": "030",
        "sId": sid,
        "tpId": tpid,
        "tId": tpid,
        "msgCode": "MSGBA0300001",
        "msgId": f"wp-{secrets.token_hex(4)}-{secrets.token_hex(3)}",
        "msgDate": _now_ms(),
        "resCode": "200",
        "resMsg": "",
        "dataFormat": "application/json",
        "severity": "0",
        "encType": "0",
        "data": {"func": 508, "arg": {}},
    }
    return topic, payload


def build_trigger_unlock(sid: str, tpid: str, pin_token: str) -> tuple[str, dict[str, Any]]:
    """등록된 영구 pin_token 으로 열림 트리거(func 407). 매 unlock 마다 이것만 재사용."""
    topic = f"ocp/{sid}/{tpid}"
    payload = {
        "version": "1.0",
        "msgType": "Q",
        "funcType": "030",
        "sId": sid,
        "tpId": tpid,
        "tId": tpid,
        "msgCode": "MSGBA0300001",
        "msgId": f"op-{_now_ms()}-{secrets.token_hex(3)}",
        "msgDate": _now_ms(),
        "resCode": "200",
        "resMsg": "",
        "dataFormat": "application/json",
        "severity": "0",
        "encType": "0",
        # authToken 필드 자체를 안 보냄(실캡처 그대로 — 생략이 맞음, PROTOCOL.md §9-2)
        "data": {"func": 407, "arg": {"locked": False, "pin": pin_token, "access": "SVR"}, "from": pin_token},
    }
    return topic, payload


def _now_ms() -> int:
    return int(time.time() * 1000)


def _epoch_ms_to_iso(ms: int) -> str | None:
    try:
        return datetime.fromtimestamp(ms / 1000).astimezone().isoformat()
    except (ValueError, OSError, OverflowError):
        return None
