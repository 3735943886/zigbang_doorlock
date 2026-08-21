"""pin 레지스트리 수동 새로고침 버튼, HA 키 초기화(위험) 버튼.

앱에서 pin 이름/카드 등록정보만 바꾸면 도어락 쪽엔 아무 MQTT 트래픽도 안 감(실측 확인) —
relay 관찰로는 그 변경을 원리적으로 절대 못 알아챈다. HA 시작시 1회 자동시딩만으론 부족해서
(그 이후 바뀐 건 재시작 전까지 반영 안 됨), 언제든 눌러서 REST 로 다시 받아오는 버튼을 둔다.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import async_refresh_pin_registry, async_reset_ha_pins
from .const import DOMAIN, HA_PIN_NAME
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(
        ZigbangRefreshPinRegistryButton(coordinator, entry, tp_id, lock_info) for tp_id, lock_info in locks.items()
    )
    async_add_entities(
        ZigbangResetHaPinsButton(coordinator, entry, tp_id, lock_info) for tp_id, lock_info in locks.items()
    )


class ZigbangRefreshPinRegistryButton(ZigbangEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_pin_registry"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZigbangCoordinator, entry: ConfigEntry, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._entry = entry
        self._attr_unique_id = f"{tp_id}_refresh_pin_registry"

    async def async_press(self) -> None:
        try:
            await async_refresh_pin_registry(self.hass, self._entry, self._tp_id)
        except Exception as err:
            raise HomeAssistantError(f"pin 레지스트리 새로고침 실패: {err}") from err


class ZigbangResetHaPinsButton(ZigbangEntity, ButtonEntity):
    """이름이 HA_PIN_NAME("ZBDL-HA-KEY")인 pin을 전부 지우고 새 키 하나로 재등록.

    manage_pins.py로 정리하려면 python을 직접 실행할 수 있는 환경이어야 하는데, HAOS 등
    쉘 접근이 어려운 설치에선 그게 안 된다 — 그런 환경에서도 HA 화면만으로 중복 키를
    정리할 수 있게 하는 최후 수단. 위험도가 높아서(지금 실제로 쓰이는 키까지 포함해서
    전부 지운 뒤 재등록하므로, 실행 중 실패하면 그 사이엔 unlock이 안 됨) 기본은
    비활성화 상태 — Settings > Devices & services > 엔티티에서 직접 활성화해야 나타난다.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "reset_ha_pins"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:key-remove"

    def __init__(self, coordinator: ZigbangCoordinator, entry: ConfigEntry, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._entry = entry
        self._attr_unique_id = f"{tp_id}_reset_ha_pins"

    async def async_press(self) -> None:
        try:
            deleted = await async_reset_ha_pins(self.hass, self._entry, self._tp_id)
        except Exception as err:
            raise HomeAssistantError(f"HA 키 초기화 실패: {err}") from err
        _LOGGER.warning(
            "HA 키 초기화 완료(tpId=%s): 이름이 %r인 pin %d개 삭제 후 새 키로 재등록함",
            self._tp_id, HA_PIN_NAME, deleted,
        )
