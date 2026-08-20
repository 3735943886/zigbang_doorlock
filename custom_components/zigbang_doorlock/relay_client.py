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

from .protocol import build_unlock_sequence

_LOGGER = logging.getLogger(__name__)

_RECONNECT_INTERVAL = 15
_UNLOCK_STEP_DELAY = 0.4  # 등록→트리거 사이(락이 pin을 반영할 시간)
_CLEANUP_DELAY = 2.0  # 정리(func 420)는 결과 무관 나중에 호출해도 무해(PROTOCOL.md §9-3)

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
    ) -> None:
        self._host = host
        self._port = port
        self._tracked_tpids = tracked_tpids
        self._on_message = on_message
        self._client: aiomqtt.Client | None = None
        self._sid_by_tpid: dict[str, str] = {}
        self._stop = asyncio.Event()

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
        """원격열림 3단계 시퀀스 실행. 정리(3단계)는 백그라운드로 넘기고 즉시 반환."""
        sid = self._sid_by_tpid.get(tpid)
        if sid is None:
            raise RelayUnlockError(
                "아직 이 도어락의 세션 정보(sId)를 관측하지 못했습니다 — "
                "락이 최근 트래픽을 보낼 때까지 잠시 후 다시 시도해주세요."
            )

        sequence = build_unlock_sequence(sid, tpid)
        (reg_topic, reg_payload), (trg_topic, trg_payload), (clean_topic, clean_payload) = sequence

        await self.publish(reg_topic, reg_payload)
        await asyncio.sleep(_UNLOCK_STEP_DELAY)
        await self.publish(trg_topic, trg_payload)

        async def _cleanup() -> None:
            await asyncio.sleep(_CLEANUP_DELAY)
            try:
                await self.publish(clean_topic, clean_payload)
            except RelayUnlockError:
                pass  # 연결이 끊겼으면 정리는 포기 — 열림 자체엔 영향 없음(PROTOCOL.md §9-3)

        asyncio.ensure_future(_cleanup())
