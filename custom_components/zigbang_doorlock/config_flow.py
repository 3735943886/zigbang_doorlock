"""config_flow: 1) 직방 계정 로그인+도어락목록 조회, 2) relay 방식 선택 — 내장(HA 가 직접 띄움, cert 만
입력) 또는 외부(따로 띄운 relay 의 observer host/port + 선택적 basic auth)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZigbangAuthError, ZigbangCloudClient, ZigbangConnectionError, generate_imei
from .const import (
    CONF_CERT_FILE,
    CONF_CERT_KEY,
    CONF_CERT_NAME,
    CONF_HOST,
    CONF_IMEI,
    CONF_LOCKS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RELAY_MODE,
    CONF_RELAY_PASSWORD,
    CONF_RELAY_USERNAME,
    CONF_USERNAME,
    DEFAULT_CERT_FILE,
    DEFAULT_CERT_KEY,
    DEFAULT_RELAY_PORT,
    DOMAIN,
    RELAY_MODE_EMBEDDED,
    RELAY_MODE_EXTERNAL,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_IMEI): str,
    }
)


_RELAY_MENU = [RELAY_MODE_EMBEDDED, RELAY_MODE_EXTERNAL]


def _embedded_schema(current: dict[str, Any]) -> vol.Schema:
    cert_name = current.get(CONF_CERT_NAME)
    return vol.Schema(
        {
            vol.Required(CONF_CERT_NAME, **({"default": cert_name} if cert_name else {})): str,
            vol.Required(CONF_CERT_FILE, default=current.get(CONF_CERT_FILE, DEFAULT_CERT_FILE)): str,
            vol.Required(CONF_CERT_KEY, default=current.get(CONF_CERT_KEY, DEFAULT_CERT_KEY)): str,
        }
    )


def _cert_files_exist(cert_file: str, cert_key: str) -> bool:
    return Path(cert_file).is_file() and Path(cert_key).is_file()


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
                    return self.async_show_menu(step_id="relay", menu_options=_RELAY_MENU)

        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA, errors=errors)

    async def async_step_embedded(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await self.hass.async_add_executor_job(_cert_files_exist, user_input[CONF_CERT_FILE], user_input[CONF_CERT_KEY]):
                return self._create_entry({CONF_RELAY_MODE: RELAY_MODE_EMBEDDED, **user_input})
            errors["base"] = "cert_not_found"

        return self.async_show_form(step_id="embedded", data_schema=_embedded_schema(user_input or {}), errors=errors)

    async def async_step_external(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await _test_relay_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self._create_entry({CONF_RELAY_MODE: RELAY_MODE_EXTERNAL, **user_input})
            errors["base"] = "relay_unreachable"

        return self.async_show_form(step_id="external", data_schema=_relay_schema(), errors=errors)

    def _create_entry(self, relay_data: dict[str, Any]) -> config_entries.FlowResult:
        return self.async_create_entry(
            title="Zigbang 도어락",
            data={**self._account_data, **relay_data, CONF_LOCKS: self._locks},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ZigbangOptionsFlow:
        return ZigbangOptionsFlow()


class ZigbangOptionsFlow(config_entries.OptionsFlow):
    """계정 재인증 없이 relay 방식(내장/외부)과 그 설정(cert 경로, 외부 relay 이사 등)만 변경."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        return self.async_show_menu(step_id="init", menu_options=_RELAY_MENU)

    async def async_step_embedded(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await self.hass.async_add_executor_job(_cert_files_exist, user_input[CONF_CERT_FILE], user_input[CONF_CERT_KEY]):
                return self.async_create_entry(title="", data={CONF_RELAY_MODE: RELAY_MODE_EMBEDDED, **user_input})
            errors["base"] = "cert_not_found"

        current = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="embedded", data_schema=_embedded_schema(current), errors=errors)

    async def async_step_external(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if await _test_relay_connection(user_input[CONF_HOST], user_input[CONF_PORT]):
                return self.async_create_entry(title="", data={CONF_RELAY_MODE: RELAY_MODE_EXTERNAL, **user_input})
            errors["base"] = "relay_unreachable"

        # 내장 모드였던 entry 엔 host/port 가 없음 — 그땐 빈 폼(최초 설정과 동일)으로.
        schema = _relay_schema(current) if CONF_HOST in current else _relay_schema()
        return self.async_show_form(step_id="external", data_schema=schema, errors=errors)
