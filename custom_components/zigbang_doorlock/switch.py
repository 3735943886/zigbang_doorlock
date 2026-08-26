"""보안설정 스위치 — 재택안심모드/매직넘버이중보안모드/이중출입인증모드/외출시실내방범모드.

전부 func 401(setAttr) 명령이고, fixtures/othermodes.capture 실측(2026-08-26) 결과 원래는 공식
앱이 클라우드에서 락 토픽(ocp/{sId}/{tpId})으로 내리는 명령(dir:cloud2dev)이다 — HA에서 직접
켜고 끌 땐 그 명령을 그대로 재현해서 우리가 클라우드 역할을 대신한다(protocol.py의 build_set_*,
relay_client.py의 async_set_mode 참조). 낙관적 갱신 없음 — lock.py의 unlock과 동일하게, 상태는
relay가 tap하는 실제 IDPEVENT(638/660/661/662) 응답으로만 갱신된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity
from .protocol import (
    build_set_2way_auth_mode,
    build_set_away_indoor_mode,
    build_set_dummy_mode,
    build_set_magic_number_mode,
)
from .relay_client import RelayClient, RelayUnlockError, SetModeBuilder


@dataclass(frozen=True, kw_only=True)
class ZigbangSwitchDescription(SwitchEntityDescription):
    builder: SetModeBuilder = build_set_dummy_mode


SWITCH_DESCRIPTIONS: tuple[ZigbangSwitchDescription, ...] = (
    ZigbangSwitchDescription(
        key="dummy_mode",
        translation_key="dummy_mode",
        icon="mdi:home-account",  # 재택안심모드
        entity_category=EntityCategory.CONFIG,
        builder=build_set_dummy_mode,
    ),
    ZigbangSwitchDescription(
        key="use_magic_number",
        translation_key="use_magic_number",
        icon="mdi:dialpad",  # 매직넘버 = 키패드 입력에 추가 코드 요구
        entity_category=EntityCategory.CONFIG,
        builder=build_set_magic_number_mode,
    ),
    ZigbangSwitchDescription(
        key="use_2way_auth",
        translation_key="use_2way_auth",
        icon="mdi:shield-account",  # 이중출입인증 = 자격증명 2단계 확인
        entity_category=EntityCategory.CONFIG,
        builder=build_set_2way_auth_mode,
    ),
    ZigbangSwitchDescription(
        key="away_indoor_armed",
        translation_key="away_indoor_armed",
        icon="mdi:motion-sensor",  # 외출시 실내방범
        entity_category=EntityCategory.CONFIG,
        builder=build_set_away_indoor_mode,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    relay: RelayClient = data["relay"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(
        ZigbangModeSwitch(coordinator, relay, tp_id, lock_info, description)
        for tp_id, lock_info in locks.items()
        for description in SWITCH_DESCRIPTIONS
    )


class ZigbangModeSwitch(ZigbangEntity, SwitchEntity):
    _attr_has_entity_name = True
    entity_description: ZigbangSwitchDescription

    def __init__(
        self,
        coordinator: ZigbangCoordinator,
        relay: RelayClient,
        tp_id: str,
        lock_info: dict[str, Any],
        description: ZigbangSwitchDescription,
    ) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self.entity_description = description
        self._relay = relay
        self._attr_unique_id = f"{tp_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self._state.get(self.entity_description.key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        try:
            await self._relay.async_set_mode(self._tp_id, self.entity_description.builder, enabled)
        except RelayUnlockError as err:
            raise HomeAssistantError(str(err)) from err
