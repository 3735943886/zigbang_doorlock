"""도어락 lock 엔티티.

원격 열림만 지원(remote lock 명령은 리버싱 안 됨 — 실기기/공식앱도 자동재잠김 타임아웃만
있고 원격으로 다시 잠그는 기능 자체가 없음, PROTOCOL.md 어디에도 그런 func 없음).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity
from .relay_client import RelayClient, RelayUnlockError


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    relay: RelayClient = data["relay"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(ZigbangLock(coordinator, relay, tp_id, lock_info) for tp_id, lock_info in locks.items())


class ZigbangLock(ZigbangEntity, LockEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "lock"

    def __init__(self, coordinator: ZigbangCoordinator, relay: RelayClient, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._relay = relay
        self._attr_unique_id = f"{tp_id}_lock"

    @property
    def is_locked(self) -> bool | None:
        return self._state.get("locked")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        return {
            "last_access": state.get("last_access"),
            "last_method": state.get("last_method"),
            "last_user_name": state.get("last_user_name"),
            "last_event_at": state.get("last_event_at"),
        }

    async def async_unlock(self, **kwargs: Any) -> None:
        try:
            await self._relay.async_unlock(self._tp_id)
        except RelayUnlockError as err:
            raise HomeAssistantError(str(err)) from err

        # 낙관적 갱신 없음 — 트리거 publish 가 성공해도 락이 실제로 열렸다는 보장은 아니라서
        # (예: resCode 400으로 거절돼도 publish 자체는 성공), 상태는 relay 가 tap 하는
        # 실제 IDPEVENT(622)/Basic-AttrGroup 로 locked=false 가 들어올 때만 바뀐다.
