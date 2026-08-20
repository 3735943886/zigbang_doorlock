"""배터리(변환값+raw)/Wi-Fi 신호 센서.

명확히 해석 가능한 값만 엔티티로 노출 — soundLevel/mode/DST 등 설정성 필드는 제어수단도
없고 의미도 불명확해 제외(대화 스레드 결론). 필요해지면 여기 description 만 추가하면 됨.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity
from .protocol import battery_raw_to_pct


@dataclass(frozen=True, kw_only=True)
class ZigbangSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] = lambda state: None


SENSOR_DESCRIPTIONS: tuple[ZigbangSensorDescription, ...] = (
    ZigbangSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda state: battery_raw_to_pct(state.get("battery_raw")),
    ),
    ZigbangSensorDescription(
        key="battery_raw",
        translation_key="battery_raw",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.get("battery_raw"),
    ),
    ZigbangSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.get("rssi"),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(
        ZigbangSensor(coordinator, tp_id, lock_info, description)
        for tp_id, lock_info in locks.items()
        for description in SENSOR_DESCRIPTIONS
    )


class ZigbangSensor(ZigbangEntity, SensorEntity):
    _attr_has_entity_name = True
    entity_description: ZigbangSensorDescription

    def __init__(
        self,
        coordinator: ZigbangCoordinator,
        tp_id: str,
        lock_info: dict[str, Any],
        description: ZigbangSensorDescription,
    ) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self.entity_description = description
        self._attr_unique_id = f"{tp_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._state)
