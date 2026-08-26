"""Zigbang Doorlock 통합 구성요소.

락 상태갱신/제어는 ../zigbang 로컬 relay 의 observer 포트를 통해 push 로 처리한다
(iot_class: local_push) — 클라우드는 상시 폴링하지 않는다.

단, pin 레지스트리(pinId->지문/카드/키패드 등 어떤 자격증명인지)와 doorlockStatusVO 기반
라이브상태(locked/battery/dummy_mode/use_magic_number/use_2way_auth/away_indoor_armed)는
REST 로도 받는다: relay observer 는 tap 이라 HA 가 뜨기 전 트래픽은 못 보고, 실측 로그
(private/logs/*ocpdataBus*.txt, ../zigbang) 확인 결과 pinInfoXXX 는 세션 부트스트랩에도 항상
오는 게 아니라 그 슬롯이 등록/터치될 때만 개별적으로 오며 실제 카드 언락 순간에도 안 실려있던
사례가 있었다. 게다가 **앱에서 pin 이름만 바꾸는 건 도어락 쪽에 아무 트래픽도 안 보낸다**(실측
확인) — relay 로는 그 변경을 원리적으로 절대 못 알아챈다. 보안설정 스위치들도 마찬가지로 HA가
막 시작했을 때는 relay 트래픽이 잡히기 전까지 unknown 상태로 남는데, membersdoorlocklist 응답의
doorlockStatusVO에 이미 이 값들이 실려있는 게 실측 확인돼서(fixtures/
membersdoorlocklist_response.json, api.py의 fetch_doorlocks 참조) 같이 REST로 시딩한다. 그래서:
  - async_setup_entry 에서 시작마다 1회 REST 로 초기 시딩(실패해도 lock/battery/rssi 등 핵심기능은
    relay 로 그대로 동작 — 아래 try/except).
  - button.py 의 "클라우드 데이터 새로고침" 버튼으로 언제든 다시 REST 로 받아올 수 있음(재시작 없이).
  - relay 의 Basic-AttrGroup/IDPEVENT push 는 그 사이 바뀐 값을 실시간으로 보조 반영.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import ZigbangCloudClient
from .const import (
    ACCESS_LABELS,
    CONF_HOST,
    CONF_IMEI,
    CONF_LOCKS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DOMAIN,
    HA_PIN_NAME,
    PIN_TYPE_LABELS,
    PLATFORMS,
    SIGNAL_EVENT,
)
from .coordinator import ZigbangCoordinator
from .protocol import extract_basic_attrgroup_patch, extract_idpevent, extract_pin_registry_patch
from .relay_client import RelayClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    locks: list[dict[str, Any]] = await _ensure_ha_pin_tokens(hass, entry, entry.data[CONF_LOCKS])
    locks_by_tpid = {lock["tp_id"]: lock for lock in locks}
    ha_pin_tokens = {tp_id: lock["ha_pin_token"] for tp_id, lock in locks_by_tpid.items()}

    initial_registries, live_status_by_tpid, already_registered, adopted_tokens, registry_seed_ok = (
        await _fetch_initial_cloud_state(hass, entry, locks_by_tpid, ha_pin_tokens)
    )
    if adopted_tokens:
        ha_pin_tokens.update(adopted_tokens)
        locks_by_tpid = {
            tp_id: ({**lock, "ha_pin_token": adopted_tokens[tp_id]} if tp_id in adopted_tokens else lock)
            for tp_id, lock in locks_by_tpid.items()
        }
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_LOCKS: list(locks_by_tpid.values())})

    coordinator = ZigbangCoordinator(hass, entry.entry_id)
    coordinator.data = {
        tp_id: {
            # REST 시딩이 이번에 성공했으면 그 값을, 실패했으면(seed_ok=False) entry.data에
            # 저장된 예전 스냅샷(최초 계정 등록 시점)으로 폴백 — 아예 없는 것보단 낫다.
            "locked": live_status_by_tpid.get(tp_id, {}).get("locked", lock.get("locked")),
            "battery_raw": live_status_by_tpid.get(tp_id, {}).get("battery_raw", lock.get("battery_raw")),
            "rssi": None,
            "pin_registry": initial_registries.get(tp_id, {}),
            "last_access": None,
            "last_method": None,
            "last_user_name": None,
            "last_pin_id": None,
            "last_unlock_at": None,
            "dummy_mode": live_status_by_tpid.get(tp_id, {}).get("dummy_mode"),
            "use_magic_number": live_status_by_tpid.get(tp_id, {}).get("use_magic_number"),
            "use_2way_auth": live_status_by_tpid.get(tp_id, {}).get("use_2way_auth"),
            "away_indoor_armed": live_status_by_tpid.get(tp_id, {}).get("away_indoor_armed"),
            "jammed": False,
        }
        for tp_id, lock in locks_by_tpid.items()
    }

    host, port = _relay_target(entry)

    def on_message(tp_id: str, payload: dict[str, Any]) -> None:
        _handle_relay_message(hass, entry.entry_id, coordinator, tp_id, payload)

    relay = RelayClient(
        host=host,
        port=port,
        tracked_tpids=set(locks_by_tpid),
        on_message=on_message,
        ha_pin_tokens=ha_pin_tokens,
        already_registered=already_registered,
        registry_seed_ok=registry_seed_ok,
    )
    relay_task = hass.loop.create_task(relay.run(), name=f"{DOMAIN}_relay_{entry.entry_id}")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "relay": relay,
        "relay_task": relay_task,
        "locks": locks_by_tpid,
    }

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["relay"].stop()
        data["relay_task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await data["relay_task"]
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _build_cloud_client(hass: HomeAssistant, entry: ConfigEntry) -> ZigbangCloudClient:
    return ZigbangCloudClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_IMEI],
    )


async def _ensure_ha_pin_tokens(hass: HomeAssistant, entry: ConfigEntry, locks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """도어락마다 HA 전용 영구 unlock pin(16-hex)이 없으면 1회 생성해서 entry.data 에 영구저장.
    entry.data 는 불변 취급이 원칙이라 직접 mutate 하지 않고 새 리스트/딕셔너리로 교체."""
    updated: list[dict[str, Any]] = []
    changed = False
    for lock in locks:
        if not lock.get("ha_pin_token"):
            lock = {**lock, "ha_pin_token": secrets.token_hex(8)}
            changed = True
        updated.append(lock)
    if changed:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_LOCKS: updated})
    return updated


def _strip_pin_tokens(registry: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """pin_token(원본 자격증명 값)은 절대 coordinator.data(엔티티가 읽는 공용 상태)에 안 둔다."""
    return {pin_id: {"pin_type": info.get("pin_type"), "pin_name": info.get("pin_name")} for pin_id, info in registry.items()}


async def _fetch_initial_cloud_state(
    hass: HomeAssistant,
    entry: ConfigEntry,
    locks_by_tpid: dict[str, dict[str, Any]],
    ha_pin_tokens: dict[str, str],
) -> tuple[dict[str, dict[int, dict[str, Any]]], dict[str, dict[str, Any]], set[str], dict[str, str], bool]:
    """모듈 docstring 참조 — 실패해도 빈 결과로 degrade, 핵심기능(lock/sensor)엔 영향 없음.
    반환: (엔티티용 공개 레지스트리, tpId별 doorlockStatusVO 기반 라이브상태(api.py의
    fetch_doorlocks 반환 dict — locked/battery_raw/dummy_mode/use_magic_number/use_2way_auth/
    away_indoor_armed), HA 영구키가 이미 등록 확인된 tpId 집합, 채택된 토큰들, 이 REST 시딩
    자체가 성공했는지).

    entry.data 에 저장된 토큰이 락과 안 맞아도, 이름이 HA_PIN_NAME 인 pin이 이미 락에 있으면
    새로 등록하지 않고 그 토큰을 채택한다 — 안 그러면 재시작마다 새 토큰이 생겨서(entry.data
    저장이 안 됐거나 재구성된 경우 등) self-heal이 매번 새 pin을 쌓는다(실측 확인된 버그,
    2026-08-20). 마지막 bool(seed_ok)이 False면 이 조회 자체가 실패한 거라, RelayClient의
    self-heal/lazy 등록이 "락에 이미 이 이름의 키가 있었는지" 확인 없이 등록하게 된다 —
    relay_client.py의 registry_seed_ok 참조."""
    public_registries: dict[str, dict[int, dict[str, Any]]] = {}
    live_status_by_tpid: dict[str, dict[str, Any]] = {}
    already_registered: set[str] = set()
    adopted_tokens: dict[str, str] = {}
    seed_ok = True
    try:
        client = _build_cloud_client(hass, entry)
        await client.login()
        for lock in await client.fetch_doorlocks():
            if lock["tp_id"] in locks_by_tpid:
                live_status_by_tpid[lock["tp_id"]] = lock
        for tp_id, lock in locks_by_tpid.items():
            raw = await client.fetch_pin_registry(lock["device_id"])
            public_registries[tp_id] = _strip_pin_tokens(raw)
            _warn_if_duplicate_ha_pins(raw, tp_id)
            if _has_ha_pin(raw, ha_pin_tokens[tp_id]):
                already_registered.add(tp_id)
                continue
            existing = _find_named_pin(raw, HA_PIN_NAME)
            if existing is not None:
                _LOGGER.info(
                    "저장된 HA pin 토큰이 락과 안 맞아서, 락에 이미 등록된 HA pin 토큰을 재사용합니다"
                    "(tpId=%s) — 새로 등록하지 않음", tp_id,
                )
                adopted_tokens[tp_id] = existing["pin_token"]
                already_registered.add(tp_id)
    except Exception as err:  # noqa: BLE001 - 여기서 실패는 치명적이지 않음(아래 docstring)
        seed_ok = False
        _LOGGER.warning("클라우드 상태 시딩 실패(카드/지문 세분화·보안설정 스위치 초기값은 안 되지만 나머지는 정상 동작): %s", err)
    return public_registries, live_status_by_tpid, already_registered, adopted_tokens, seed_ok


def _has_ha_pin(registry: dict[int, dict[str, Any]], ha_pin_token: str) -> bool:
    return any(info.get("pin_token") == ha_pin_token for info in registry.values())


def _find_named_pin(registry: dict[int, dict[str, Any]], pin_name: str) -> dict[str, Any] | None:
    return next((info for info in registry.values() if info.get("pin_name") == pin_name and info.get("pin_token")), None)


def _find_all_named_pins(registry: dict[int, dict[str, Any]], pin_name: str) -> list[tuple[int, str]]:
    """이름이 pin_name 인 pin 전부 -> [(pinId, pin_token), ...]. "HA 키 초기화" 버튼이
    지울 대상을 정확히 고르는 용도(_find_named_pin 은 하나만 리턴해서 중복 청소엔 안 맞음)."""
    return [
        (pin_id, info["pin_token"])
        for pin_id, info in registry.items()
        if info.get("pin_name") == pin_name and info.get("pin_token")
    ]


def _warn_if_duplicate_ha_pins(registry: dict[int, dict[str, Any]], tp_id: str) -> None:
    """REST로 락의 실제 pin 목록을 확인할 때마다 호출 — 이름이 HA_PIN_NAME인 pin이 2개
    이상이면 경고만 하고 자동으로 지우지 않는다(삭제는 위험도가 높아서 "HA 키 초기화"
    버튼으로 사람이 직접 눌러야만 실행됨, button.py 참조)."""
    named = _find_all_named_pins(registry, HA_PIN_NAME)
    if len(named) > 1:
        _LOGGER.warning(
            "이름이 %r인 pin이 %d개 있습니다(pinId=%s, tpId=%s) — REST 시딩 실패 중 self-heal이"
            " 중복 등록했을 가능성이 있습니다. '%s 키 초기화' 버튼으로 정리하거나"
            " helper/manage_pins.py로 직접 확인해서 정리하는 걸 권장합니다.",
            HA_PIN_NAME, len(named), [pin_id for pin_id, _ in named], tp_id, HA_PIN_NAME,
        )


async def async_refresh_cloud_data(hass: HomeAssistant, entry: ConfigEntry, tp_id: str) -> None:
    """button.py 의 "클라우드 데이터 새로고침"에서 호출 — pin 레지스트리뿐 아니라
    doorlockStatusVO 기반 라이브상태(locked/battery/dummy_mode/use_magic_number/use_2way_auth/
    away_indoor_armed)도 같이 갱신한다(fixtures/membersdoorlocklist_response.json 실측 —
    REST 응답에 이미 이 값들이 다 있음, api.py의 fetch_doorlocks 참조). 여기선 실패를 삼키지
    않고 그대로 올려서 버튼을 누른 사용자에게 실패가 보이게 한다(시작시 자동시딩과 반대 정책).
    REST 로 다시 받아본 결과 HA pin 이 안 보이면(사용자가 앱에서 직접 지웠거나 락 초기화 등)
    바로 재등록까지 한다 — 다음 unlock 을 기다리지 않음."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    lock_info = data["locks"][tp_id]
    relay: RelayClient = data["relay"]

    client = _build_cloud_client(hass, entry)
    await client.login()

    for lock in await client.fetch_doorlocks():
        if lock["tp_id"] == tp_id:
            _merge_state(coordinator, tp_id, {
                "locked": lock.get("locked"),
                "battery_raw": lock.get("battery_raw"),
                "dummy_mode": lock.get("dummy_mode"),
                "use_magic_number": lock.get("use_magic_number"),
                "use_2way_auth": lock.get("use_2way_auth"),
                "away_indoor_armed": lock.get("away_indoor_armed"),
            })
            break

    registry = await client.fetch_pin_registry(lock_info["device_id"])
    _replace_pin_registry(coordinator, tp_id, _strip_pin_tokens(registry))
    relay.mark_registry_seed_ok()
    _warn_if_duplicate_ha_pins(registry, tp_id)

    if _has_ha_pin(registry, lock_info["ha_pin_token"]):
        return

    existing = _find_named_pin(registry, HA_PIN_NAME)
    if existing is not None:
        _LOGGER.info(
            "저장된 HA pin 토큰이 락과 안 맞아서, 락에 이미 등록된 HA pin 토큰을 재사용합니다"
            "(tpId=%s) — 새로 등록하지 않음", tp_id,
        )
        adopted = existing["pin_token"]
        new_lock_info = {**lock_info, "ha_pin_token": adopted}
        data["locks"] = {**data["locks"], tp_id: new_lock_info}
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_LOCKS: list(data["locks"].values())}
        )
        relay.adopt_token(tp_id, adopted)
        return

    _LOGGER.info("HA pin 이 락에 없어서 재등록합니다(tpId=%s)", tp_id)
    await relay.ensure_registered(tp_id)


async def async_reset_ha_pins(hass: HomeAssistant, entry: ConfigEntry, tp_id: str) -> int:
    """"HA 키 초기화" 버튼(button.py, 기본 비활성화) 전용 — 이름이 HA_PIN_NAME인 pin을 락에서
    전부 지우고 새 토큰으로 딱 하나만 재등록한다. 중복이 쌓였을 때 python을 직접 실행하기
    어려운 환경(HAOS 등)에서도 HA 안에서 정리할 수 있게 하는 최후 수단이라 위험도가 높다:
    - 지금 실제로 쓰이고 있는 키까지 포함해서 이름이 같은 건 전부 지운다(그래서 반드시
      뒤이어 새 키를 등록까지 한다 — 지우기만 하고 끝나면 그 사이엔 unlock이 안 된다).
    - 같은 토큰을 재등록하는 건 PROTOCOL.md §9가 미검증이라고 경고한 영역이라(protocol.py의
      build_register_key 참조) 재사용하지 않고 새 토큰을 만든다.
    반환값: 삭제한 pin 개수."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    lock_info = data["locks"][tp_id]
    relay: RelayClient = data["relay"]

    client = _build_cloud_client(hass, entry)
    await client.login()
    registry = await client.fetch_pin_registry(lock_info["device_id"])
    targets = _find_all_named_pins(registry, HA_PIN_NAME)

    for _pin_id, pin_token in targets:
        await relay.delete_pin(tp_id, pin_token)

    new_token = secrets.token_hex(8)
    new_lock_info = {**lock_info, "ha_pin_token": new_token}
    data["locks"] = {**data["locks"], tp_id: new_lock_info}
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LOCKS: list(data["locks"].values())}
    )
    relay.replace_ha_pin_token(tp_id, new_token)
    await relay.ensure_registered(tp_id)

    registry_after = await client.fetch_pin_registry(lock_info["device_id"])
    _replace_pin_registry(coordinator, tp_id, _strip_pin_tokens(registry_after))

    return len(targets)


def _relay_target(entry: ConfigEntry) -> tuple[str, int]:
    # options 에서 설정 변경 가능(OptionsFlow) — 없으면 최초 설정값(entry.data) 사용.
    host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
    port = entry.options.get(CONF_PORT, entry.data[CONF_PORT])
    return host, port


def _handle_relay_message(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: ZigbangCoordinator,
    tp_id: str,
    payload: dict[str, Any],
) -> None:
    msg_code = payload.get("msgCode")
    data = payload.get("data")
    if not isinstance(data, dict):
        return

    if msg_code == "Basic-AttrGroup":
        registry_patch = extract_pin_registry_patch(data)
        if registry_patch:
            _merge_pin_registry(coordinator, tp_id, registry_patch)
        patch = extract_basic_attrgroup_patch(data)
        if patch:
            _merge_state(coordinator, tp_id, patch)
        return

    if msg_code == "IDPEVENT":
        event = extract_idpevent(data, payload.get("msgDate"))
        if event is None:
            return

        if "event_type" not in event:
            # 638/660/661/662(보안설정 스위치 상태에코) — 활동기록이 아니라 연속상태라
            # event.py 활동엔티티로는 안 보내고 coordinator만 갱신한다. protocol.py 참조.
            _merge_state(coordinator, tp_id, event["patch"])
            return

        patch: dict[str, Any] = dict(event.get("patch") or {})
        if "locked" in event:
            patch["locked"] = event["locked"]
            # 622(잠금상태변경)는 락이 다시 정상 상태를 보고한 거라, 직전에 652(재밍)로 선 jammed
            # 플래그를 여기서 해제한다 — 652 자체엔 "풀림" 신호가 없어서 다음 상태보고를 그 대용으로
            # 쓴다(lock.py의 is_jammed 참조).
            patch["jammed"] = False

            access = event.get("access")
            pin_id = event.get("pin_id")
            pin_info = None
            if pin_id is not None:
                registry = coordinator.data.get(tp_id, {}).get("pin_registry", {})
                pin_info = registry.get(pin_id)

            # 레지스트리의 pinType이 이번 이벤트의 access와 일치할 때만 그 자격증명으로 확정한다.
            # access 값 자체가 대부분 pinType 코드와 동일하게 오므로(실측 2026-08-20: RFC=카드/키태그,
            # FGP=지문, MST=마스터/번호코드) 이 매칭 하나로 하드코딩된 access 허용목록 없이도 지금 아는
            # 코드는 물론 앞으로 나올 새 코드까지 커버된다. 불일치(RMC/AUTO/INDOOR처럼 pinId가 다른
            # 슬롯을 가리키거나 필러인 경우 등)는 자연스럽게 폴백으로 빠진다.
            #
            # SVR은 예외: "무슨 자격증명이냐"가 아니라 "원격으로 열렸다"는 채널을 뜻하는 값이라
            # pinType과 애초에 같을 수가 없다(실측 2026-08-21: HA가 등록해둔 영구 NFC 키로 407
            # 트리거하면 access="SVR", pinId/pin은 그 키의 진짜 값 그대로 옴 — 캡처에서 pin 필드가
            # 등록시 값과 정확히 일치해 확인). 예전엔 이걸 "임시키라 못 믿는다"고 오판해서 SVR 오픈은
            # 전부 unknown으로 떨어졌었는데, pinId 자체는 신뢰 가능한 걸로 실측 확인됐다 — method는
            # 그대로 "remote"로 두고(실제로 원격 트리거니까) name/pin_type만 레지스트리에서 채운다.
            if pin_info and access == "SVR":
                method = ACCESS_LABELS.get(access, access)
                user_name = pin_info.get("pin_name")
                event["pin_type"] = pin_info.get("pin_type")
                event["pin_name"] = pin_info.get("pin_name")
            elif pin_info and pin_info.get("pin_type") == access:
                method = PIN_TYPE_LABELS.get(access, access)
                user_name = pin_info.get("pin_name")
                event["pin_type"] = access
                event["pin_name"] = pin_info.get("pin_name")
            else:
                method = ACCESS_LABELS.get(access, access)
                user_name = None
                event["pin_type"] = None
                event["pin_name"] = None

            # 잠길 때(AUTO 자동재잠김/MNU 수동잠금 등)는 pinId가 대부분 필러라 method/name이
            # 항상 unknown으로 나온다 — 그걸로 직전 unlock의 "누가/무엇으로 열었는지" 상태를
            # 덮어쓰면 자동재잠김 한 번에 몇 초 만에 사라져서(실측 확인, 2026-08-21), 열릴 때
            # (locked=False)만 이 상태들을 갱신한다. event 자체(활동기록 엔티티용, event.py)는
            # 이 판별과 무관하게 이번 이벤트의 실제 결과를 그대로 담는다.
            if not event["locked"]:
                patch["last_access"] = access
                patch["last_pin_id"] = pin_id
                patch["last_method"] = method
                patch["last_user_name"] = user_name
                patch["last_unlock_at"] = event.get("at")

        _merge_state(coordinator, tp_id, patch)
        async_dispatcher_send(hass, SIGNAL_EVENT.format(entry_id, tp_id), event)
        return


def _merge_state(coordinator: ZigbangCoordinator, tp_id: str, patch: dict[str, Any]) -> None:
    new_data = dict(coordinator.data)
    new_data[tp_id] = {**new_data.get(tp_id, {}), **patch}
    coordinator.async_set_updated_data(new_data)


def _merge_pin_registry(coordinator: ZigbangCoordinator, tp_id: str, registry_patch: dict[int, dict[str, Any]]) -> None:
    """relay 로부터 온 부분patch를 기존 레지스트리에 얹는다(union) — 삭제는 반영 못 함(그래서
    button.py 의 수동새로고침은 이거 대신 _replace_pin_registry 를 씀)."""
    new_data = dict(coordinator.data)
    state = dict(new_data.get(tp_id, {}))
    registry = {**state.get("pin_registry", {}), **registry_patch}
    state["pin_registry"] = registry
    new_data[tp_id] = state
    coordinator.async_set_updated_data(new_data)


def _replace_pin_registry(coordinator: ZigbangCoordinator, tp_id: str, registry: dict[int, dict[str, Any]]) -> None:
    """REST 로 받은 전체 목록으로 통째로 교체 — 앱에서 pin 을 삭제한 경우도 정확히 반영."""
    new_data = dict(coordinator.data)
    state = dict(new_data.get(tp_id, {}))
    state["pin_registry"] = registry
    new_data[tp_id] = state
    coordinator.async_set_updated_data(new_data)
