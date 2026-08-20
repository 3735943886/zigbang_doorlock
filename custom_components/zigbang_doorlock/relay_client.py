"""../zigbang relay 의 observer 포트(기본 9883, 평문 MQTT, 무인증) 클라이언트.

observer 는 락↔클라우드 실트래픽을 tap 해서 그대로 재발행하는 포트라 `#` 구독만으로
전 디바이스 이벤트가 다 들어온다(RUNBOOK.md §2). 8883/18883(1:1 릴레이 포트)에 직접
붙으면 안 됨 — 그건 매 접속마다 독립 업스트림 세션을 새로 만들 뿐 락 트래픽이 안 보임.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiomqtt

from .protocol import build_register_key, build_trigger_unlock, build_wake

_LOGGER = logging.getLogger(__name__)

_RECONNECT_INTERVAL = 15
_REGISTER_SETTLE_DELAY = 1.0  # 영구키 등록(1회성)→트리거 사이, 락이 pin을 반영할 시간
# wake(508)→트리거(407) 사이 지연. 실캡처(앱)에서는 클라우드 왕복 지연 때문에 자연스럽게
# 이 정도 간격이 생기는데, 우리는 로컬 relay라 딜레이 없이 바로 붙여보내면 락이 508을 아직
# 처리 중일 때 407이 도착해서 resCode 400으로 거절당함(실측 확인) — 그 간격을 흉내낸다.
_WAKE_SETTLE_DELAY = 0.5

OnMessage = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class RelayUnlockError(Exception):
    """sId 를 아직 모르거나(디바이스 관측 전) publish 실패."""


class RelayClient:
    def __init__(
        self,
        host: str,
        port: int,
        tracked_tpids: set[str],
        on_message: OnMessage,
        ha_pin_tokens: dict[str, str],
        already_registered: set[str],
    ) -> None:
        self._host = host
        self._port = port
        self._tracked_tpids = tracked_tpids
        self._on_message = on_message
        self._client: aiomqtt.Client | None = None
        self._sid_by_tpid: dict[str, str] = {}
        self._stop = asyncio.Event()
        # tpId -> 이 통합구성요소 전용 영구 NFC 키 토큰(config_flow/__init__.py 에서 1회 생성,
        # entry.data 에 영구저장). 매 unlock 마다 새로 안 만들고 이걸 계속 재사용한다.
        self._ha_pin_tokens = ha_pin_tokens
        # REST(getdoorkeys)로 이미 등록 확인된 tpId 집합 — 여기 없는 애만 첫 unlock 때 등록(408)부터.
        self._registered = set(already_registered)

    async def run(self) -> None:
        """상시 재연결 루프. hass.async_create_background_task 등으로 백그라운드 실행."""
        while not self._stop.is_set():
            try:
                async with aiomqtt.Client(hostname=self._host, port=self._port) as client:
                    self._client = client
                    await client.subscribe("#")
                    _LOGGER.info("relay observer 연결됨: %s:%s", self._host, self._port)
                    async for message in client.messages:
                        await self._handle_message(message.payload)
            except aiomqtt.MqttError as err:
                _LOGGER.warning("relay observer 연결 끊김(%s:%s), %s초 후 재시도: %s", self._host, self._port, _RECONNECT_INTERVAL, err)
            except Exception:  # noqa: BLE001 - 재연결 루프는 죽으면 안 됨
                _LOGGER.exception("relay observer 처리 중 예외")
            finally:
                self._client = None
            if self._stop.is_set():
                break
            await asyncio.sleep(_RECONNECT_INTERVAL)

    async def stop(self) -> None:
        self._stop.set()

    async def _handle_message(self, raw_payload: bytes) -> None:
        try:
            payload = json.loads(raw_payload)
        except (ValueError, TypeError):
            return  # 비JSON 프레임(하트비트 등) — 무시
        if not isinstance(payload, dict):
            return

        tpid = payload.get("tpId")
        if tpid not in self._tracked_tpids:
            return

        sid = payload.get("sId")
        if sid:
            self._sid_by_tpid[tpid] = sid

        result = self._on_message(tpid, payload)
        if asyncio.iscoroutine(result):
            await result

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            raise RelayUnlockError("relay observer 에 연결돼있지 않습니다")
        await client.publish(topic, payload=json.dumps(payload).encode(), qos=0)

    async def async_unlock(self, tpid: str) -> None:
        """영구 HA 키로 열림. 등록 안 확인된 tpId 만 이번 호출에서 먼저 등록(1회성)하고,
        그 이후로는 매번 wake(508)+트리거(407)만 보낸다 — 등록/삭제를 반복하던 예전 방식보다
        단순하고, pin 슬롯이 매번 쌓이는 문제도 구조적으로 없다."""
        sid = self._sid_by_tpid.get(tpid)
        if sid is None:
            raise RelayUnlockError(
                "아직 이 도어락의 세션 정보(sId)를 관측하지 못했습니다 — "
                "락이 최근 트래픽을 보낼 때까지 잠시 후 다시 시도해주세요."
            )

        pin_token = self._ha_pin_tokens[tpid]

        if tpid not in self._registered:
            reg_topic, reg_payload = build_register_key(sid, tpid, pin_token)
            await self.publish(reg_topic, reg_payload)
            await asyncio.sleep(_REGISTER_SETTLE_DELAY)
            self._registered.add(tpid)

        # func 508(빈 arg)을 407 직전에 항상 먼저 보내야 함 — 실캡처 확인, protocol.py의
        # build_wake 참조. 이거 빠뜨리면 publish 자체는 성공해도 락이 407을 무시해서 안 열림.
        wake_topic, wake_payload = build_wake(sid, tpid)
        await self.publish(wake_topic, wake_payload)
        await asyncio.sleep(_WAKE_SETTLE_DELAY)

        trg_topic, trg_payload = build_trigger_unlock(sid, tpid, pin_token)
        await self.publish(trg_topic, trg_payload)
