"""config_flow: 1) 직방 계정 로그인+도어락목록 조회, 2) relay 서버 host/port(+선택적 basic auth)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZigbangAuthError, ZigbangCloudClient, ZigbangConnectionError, generate_imei
from .const import (
    CONF_HOST,
    CONF_IMEI,
    CONF_LOCKS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RELAY_PASSWORD,
    CONF_RELAY_USERNAME,
    CONF_USERNAME,
    DEFAULT_RELAY_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_IMEI): str,
    }
)


def _relay_schema(current: dict[str, Any] | None = None) -> vol.Schema:
    # relay_username/relay_password는 zigbang addon 0.1.5+의 observer basic auth 대응 —
    # 둘 다 비워두면(기본값) 예전처럼 무인증으로 접속(RelayClient 참조).
    if current is None:
        return vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_RELAY_PORT): int,
                vol.Optional(CONF_RELAY_USERNAME, default=""): str,
                vol.Optional(CONF_RELAY_PASSWORD, default=""): str,
            }
        )
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=current[CONF_HOST]): str,
            vol.Required(CONF_PORT, default=current[CONF_PORT]): int,
            vol.Optional(CONF_RELAY_USERNAME, default=current.get(CONF_RELAY_USERNAME, "")): str,
            vol.Optional(CONF_RELAY_PASSWORD, default=current.get(CONF_RELAY_PASSWORD, "")): str,
        }
    )


async def _test_relay_connection(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


class ZigbangConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._account_data: dict[str, Any] = {}
        self._locks: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            imei = user_input.get(CONF_IMEI) or generate_imei()
            session = async_get_clientsession(self.hass)
            client = ZigbangCloudClient(session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD], imei)
            try:
                locks = await client.fetch_doorlocks()
            except ZigbangAuthError:
                errors["base"] = "invalid_auth"
            except ZigbangConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if not locks:
                    errors["base"] = "no_locks_found"
                else:
                    await self.async_set_unique_id(client.member_id)
                    self._abort_if_unique_id_configured()
                    self._account_data = {
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_IMEI: imei,
                    }
                    self._locks = locks
                    return await self.async_step_relay()

        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA, errors=errors)

    async def async_step_relay(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await _test_relay_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(
                    title="Zigbang 도어락",
                    data={**self._account_data, **user_input, CONF_LOCKS: self._locks},
                )
            errors["base"] = "relay_unreachable"

        return self.async_show_form(step_id="relay", data_schema=_relay_schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ZigbangOptionsFlow:
        return ZigbangOptionsFlow()


class ZigbangOptionsFlow(config_entries.OptionsFlow):
    """relay가 이사가도(IP/포트 변경) 계정 재인증 없이 host/port만 수정."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if await _test_relay_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(title="", data=user_input)
            errors["base"] = "relay_unreachable"

        return self.async_show_form(step_id="init", data_schema=_relay_schema(current), errors=errors)
