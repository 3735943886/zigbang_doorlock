"""push 전용 DataUpdateCoordinator.

relay observer 로부터 이벤트가 올 때마다 async_set_updated_data() 로 밀어넣는 용도라
update_interval=None(자체 폴링 없음) — HA 의 push 통합구성요소 표준 패턴.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZigbangCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{entry_id}", update_interval=None)
        self.data = {}
