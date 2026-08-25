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

from .protocol import build_delete_key, build_register_key, build_trigger_unlock, build_wake

_LOGGER = logging.getLogger(__name__)

_RECONNECT_INTERVAL = 15
# funcType 030 Q(408 등록/420 삭제/508 wake) 완료 ACK 대기 상한. 실캡처(fixtures/
# r5c_publishes.jsonl #1-2, #47-50/#65-68, #77-80, #89-90)에서 매번 락이 funcType 030/
# msgCode MSGBA0300002 로 같은 msgId를 실어 ACK를 보내고(359ms~1.18초 실측), 앱은 다음
# 명령을 이 ACK 이후에만 보낸다 — 고정 딜레이가 아니라 ACK 자체를 기다리는 것. 이 타임아웃은
# qos0라 ACK가 유실됐을 때의 안전판일 뿐, 정상 경로에서는 ACK가 오는 즉시 진행된다.
_ACK_TIMEOUT = 1.5

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
        registry_seed_ok: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._tracked_tpids = tracked_tpids
        self._on_message = on_message
        self._client: aiomqtt.Client | None = None
        self._sid_by_tpid: dict[str, str] = {}
        self._stop = asyncio.Event()
        # msgId -> 응답(funcType 030 A/MSGBA0300002) 대기용 future. publish_and_wait_ack 참조.
        self._pending_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # tpId -> 이 통합구성요소 전용 영구 NFC 키 토큰(config_flow/__init__.py 에서 1회 생성,
        # entry.data 에 영구저장). 매 unlock 마다 새로 안 만들고 이걸 계속 재사용한다.
        self._ha_pin_tokens = ha_pin_tokens
        # REST(getdoorkeys)로 이미 등록 확인된 tpId 집합 — 여기 없는 애는 락 트래픽이 처음
        # 관측되는 즉시(self-heal), 또는 첫 unlock/수동새로고침(ensure_registered) 때 등록(408)됨.
        self._registered = set(already_registered)
        # HA 시작시 REST 시딩(__init__.py:_fetch_initial_pin_registries)이 실패해서 "락에 이미
        # HA pin이 있는지" 자체를 확인 못한 상태인지 여부. False면 self-heal/lazy 등록이 락의
        # 실제 상태를 모른 채 등록하는 거라 기존 키와 중복될 수 있다 — 이후 REST 조회가 성공하면
        # (수동 새로고침 등) mark_registry_seed_ok로 갱신된다.
        self._registry_seed_ok = registry_seed_ok

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
            if tpid not in self._registered:
                # REST 로 시딩할 때(HA 재시작 등) 이 tpId 의 HA pin 이 락에 없는 걸로 확인됐던
                # 경우 — unlock 을 기다리지 않고, 락 트래픽이 처음 관측되는 즉시 self-heal 등록.
                # (예: 사용자가 앱에서 HA 키를 직접 지웠거나 락이 초기화된 경우 대비)
                await self._self_heal_registration(tpid, sid)

        # 락→클라우드 funcType 030 A(msgCode MSGBA0300002)는 우리가 보낸 Q(508/407/408/420)의
        # 완료 ACK — 같은 msgId로 되돌아온다(실캡처 확인). publish_and_wait_ack가 기다리는 중이면 깨운다.
        if payload.get("msgType") == "A" and payload.get("funcType") == "030":
            fut = self._pending_acks.get(payload.get("msgId"))
            if fut is not None and not fut.done():
                fut.set_result(payload)

        result = self._on_message(tpid, payload)
        if asyncio.iscoroutine(result):
            await result

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            raise RelayUnlockError("relay observer 에 연결돼있지 않습니다")
        await client.publish(topic, payload=json.dumps(payload).encode(), qos=0)

    async def _publish_and_wait_ack(self, topic: str, payload: dict[str, Any], timeout: float = _ACK_TIMEOUT) -> None:
        """publish 후 같은 msgId로 오는 funcType 030 A(MSGBA0300002) ACK을 기다린다.

        qos0라 ACK이 유실될 수 있으니 timeout은 강제 대기가 아니라 안전판 — 응답이 안 오면
        경고만 남기고 그냥 진행한다(예전 고정 sleep과 동일한 최악의 경우 동작)."""
        msg_id = payload["msgId"]
        func = payload.get("data", {}).get("func")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_acks[msg_id] = fut
        try:
            await self.publish(topic, payload)
            try:
                await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                _LOGGER.warning("func %s ACK 타임아웃(msgId=%s, %.1fs) — 응답 없이 진행", func, msg_id, timeout)
        finally:
            self._pending_acks.pop(msg_id, None)

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

        if tpid not in self._registered:
            await self._register_now(tpid, sid)

        pin_token = self._ha_pin_tokens[tpid]

        # func 508(빈 arg)을 407 직전에 항상 먼저 보내야 함 — 실캡처 확인, protocol.py의
        # build_wake 참조. 이거 빠뜨리면 publish 자체는 성공해도 락이 407을 무시해서 안 열림.
        # 508의 완료 ACK(같은 msgId로 옴)를 기다린 뒤에 407을 보낸다 — 실캡처 순서 그대로.
        wake_topic, wake_payload = build_wake(sid, tpid)
        await self._publish_and_wait_ack(wake_topic, wake_payload)

        trg_topic, trg_payload = build_trigger_unlock(sid, tpid, pin_token)
        await self.publish(trg_topic, trg_payload)

    def adopt_token(self, tpid: str, pin_token: str) -> None:
        """락에 이미 등록돼있는(이름=HA_PIN_NAME) pin을 REST로 새로 발견했을 때 호출 — 새로
        등록하는 대신 그 토큰을 그대로 갈아끼운다. __init__.py 참조."""
        self._ha_pin_tokens[tpid] = pin_token
        self._registered.add(tpid)

    def mark_registry_seed_ok(self) -> None:
        """REST 조회가 (재시작 이후 언젠가) 성공적으로 한 번이라도 끝나면 호출 — 그 뒤로는
        self-heal/lazy 등록이 더 이상 "락 상태를 모른 채 등록한다"고 경고하지 않는다."""
        self._registry_seed_ok = True

    async def delete_pin(self, tpid: str, pin_token: str) -> None:
        """지정한 pin_token을 락에서 삭제(func 420) — "HA 키 초기화" 버튼 전용. 등록(408)과
        달리 이 저장소에서 실사용 검증된 적 없는 위험한 동작이라, 호출측(button.py)에서
        REST로 정확히 확인한 pin_token만 넘겨야 한다."""
        sid = self._sid_by_tpid.get(tpid)
        if sid is None:
            raise RelayUnlockError(
                "아직 이 도어락의 세션 정보(sId)를 관측하지 못했습니다 — "
                "락이 최근 트래픽을 보낼 때까지 잠시 후 다시 시도해주세요."
            )
        topic, payload = build_delete_key(sid, tpid, pin_token)
        await self._publish_and_wait_ack(topic, payload)

    def replace_ha_pin_token(self, tpid: str, new_token: str) -> None:
        """"HA 키 초기화" 버튼이 기존 키들을 전부 지운 뒤, 새 토큰으로 교체하고 미등록 상태로
        되돌린다 — 이후 ensure_registered를 부르면 이 새 토큰으로 다시 등록(408)된다. 이미
        REST로 락 상태를 직접 확인한 뒤 부르는 거라 seed_ok도 같이 확정한다."""
        self._ha_pin_tokens[tpid] = new_token
        self._registered.discard(tpid)
        self._registry_seed_ok = True

    async def ensure_registered(self, tpid: str) -> None:
        """REST 재조회(수동 새로고침 버튼)로 이 tpId 의 HA pin 이 락에서 안 보이면 호출 —
        이미 등록 확인된 상태면 아무것도 안 하고, 아니면 지금 바로 등록(408)만 보낸다
        (wake/trigger 는 안 보냄 — 문을 여는 부작용 없이 키만 복구)."""
        if tpid in self._registered:
            return
        sid = self._sid_by_tpid.get(tpid)
        if sid is None:
            raise RelayUnlockError(
                "아직 이 도어락의 세션 정보(sId)를 관측하지 못했습니다 — "
                "락이 최근 트래픽을 보낼 때까지 잠시 후 다시 시도해주세요."
            )
        await self._register_now(tpid, sid)

    async def _self_heal_registration(self, tpid: str, sid: str) -> None:
        try:
            await self._register_now(tpid, sid)
        except RelayUnlockError as err:
            _LOGGER.warning("HA pin 자동 재등록 실패(tpId=%s): %s", tpid, err)

    async def _register_now(self, tpid: str, sid: str) -> None:
        if not self._registry_seed_ok:
            # REST 시딩 실패 상태에서 등록하는 거라, 락에 이미 같은 이름의 키가 있었는지
            # 확인할 방법이 없었다(로컬 우선 동작이 이 프로젝트의 핵심 전제라 REST가 안 될 때도
            # 등록 자체는 그대로 진행한다) — 중복이면 다음 REST 조회 때 별도로 다시 경고한다.
            _LOGGER.warning(
                "REST로 기존 pin 목록을 확인하지 못한 상태에서 HA 키를 새로 등록합니다(tpId=%s) — "
                "락에 이미 이 이름의 키가 있었다면 중복 등록일 수 있습니다.", tpid,
            )
        pin_token = self._ha_pin_tokens[tpid]
        reg_topic, reg_payload = build_register_key(sid, tpid, pin_token)
        await self._publish_and_wait_ack(reg_topic, reg_payload)
        self._registered.add(tpid)
