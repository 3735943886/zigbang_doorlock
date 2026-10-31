"""내장 relay — 별도 Docker/App 없이 HA 가 zigbang relay 바이너리를 subprocess 로 직접 띄운다.

바이너리는 PyPI 패키지 zigbang-relay(플랫폼 wheel, musl 정적바이너리 + 기본 rules)로 받는다 —
외부 relay 모드 사용자는 안 받아도 되게 manifest requirements 가 아니라 내장 모드일 때만
async_process_requirements 로 설치한다. 설정은 RELAY_CONFIG_JSON 환경변수로 넘겨서(`relay env`)
설정파일을 따로 안 만든다.

relay 는 HA(전체 프로세스)당 하나만 뜬다 — 여러 config entry(계정)가 내장 모드면 같은 relay 를
공유(refcount)하고 설정은 처음 띄운 entry 기준. observer 는 127.0.0.1 의 빈 포트에만 열어서
평문/무인증 포트가 바깥에 노출될 일 자체가 없다.

relay 바이너리 동작 중 실측으로 확인한 것 — 아래 구현이 이걸 전제로 한다:
  - (v0.1.5 이하) cert_file/cert_key 가 없으면 조용히 자체서명 cert 를 만들어 **그 경로에 덮어쓴다**
    (기존 key 까지). 0.1.6 부터는 에러로 기동 실패하지만, HAOS 의 /ssl 은 다른 App 들과 공유하는
    경로라 띄우기 전에 두 파일이 다 있는지 여기서도 확인한다(사용자에게 더 명확한 에러이기도 함).
  - (v0.1.5 이하) listen/observer 포트 bind 실패해도 프로세스는 안 죽고 `✗ FAIL @ ...` 로그만
    남긴다. 0.1.6 부터는 에러로 종료 — 기동 직후 stderr 의 `✗ FAIL` 과 조기종료(EOF) 둘 다 보고
    판정하므로 어느 버전이든 동작한다.
  - 기동마다 log_dir 에 relay-<epoch>.log/.capture 를 새로 만든다 — 오래된 건 기동시 정리.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import re
import socket
from pathlib import Path
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.requirements import async_process_requirements

from .const import (
    DOMAIN,
    EMBEDDED_LISTEN_MQTTS,
    EMBEDDED_LISTEN_UDP,
    RELAY_REQUIREMENT,
    UPSTREAM_MQTTS,
    UPSTREAM_UDP,
)

_LOGGER = logging.getLogger(__name__)

_DATA_KEY = f"{DOMAIN}_embedded_relay"
_STARTUP_GRACE = 2.0  # bind 실패(✗ FAIL)는 기동 직후 수 ms 안에 찍힘 — 넉넉히 기다린 뒤 판정
_RESTART_BACKOFF = (5, 15, 30, 60)
_STOP_TIMEOUT = 5
_KEEP_LOG_FILES = 5  # .log/.capture 각각
_LINE_PREFIX = re.compile(r"^\[\s*\d+ms\]\s*")


class EmbeddedRelayError(HomeAssistantError):
    """내장 relay 를 못 띄움(패키지 설치 실패, cert 없음, 포트 사용중 등)."""


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _prepare_dirs(base: Path) -> None:
    (base / "state").mkdir(parents=True, exist_ok=True)
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for suffix in ("*.log", "*.capture"):
        stale = sorted(logs.glob(suffix), key=lambda p: p.stat().st_mtime, reverse=True)[_KEEP_LOG_FILES:]
        for path in stale:
            with contextlib.suppress(OSError):
                path.unlink()


def _missing_cert_files(cert_file: str, cert_key: str) -> list[str]:
    return [path for path in (cert_file, cert_key) if not Path(path).is_file()]


class EmbeddedRelay:
    def __init__(self, hass: HomeAssistant, cert_name: str, cert_file: str, cert_key: str) -> None:
        self._hass = hass
        self._cert_name = cert_name
        self._cert_file = cert_file
        self._cert_key = cert_key
        self._base_dir = Path(hass.config.path(DOMAIN, "relay"))
        self._bin: str | None = None
        self._env: dict[str, str] = {}
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self.observer_port = 0

    def _config_json(self, rules_dir: str) -> str:
        cfg: dict[str, Any] = {
            "log_level": "info",
            "observer": f"127.0.0.1:{self.observer_port}",
            "rules_file": rules_dir,
            "replay_store": str(self._base_dir / "state" / "replay.json"),
            "log_dir": str(self._base_dir / "logs"),
            "cert_file": self._cert_file,
            "cert_key": self._cert_key,
            "cert_name": self._cert_name,
            "upstream_insecure": True,
            "route": [
                {"name": "mqtts", "proto": "mqtts", "mode": "proxy", "listen": EMBEDDED_LISTEN_MQTTS, "upstream": UPSTREAM_MQTTS},
                {"name": "holepunch", "proto": "udp", "mode": "proxy", "listen": EMBEDDED_LISTEN_UDP, "upstream": UPSTREAM_UDP},
            ],
        }
        return json.dumps(cfg)

    async def start(self) -> None:
        missing = await self._hass.async_add_executor_job(_missing_cert_files, self._cert_file, self._cert_key)
        if missing:
            # relay 에 그대로 넘기면 자체서명 cert 로 이 경로들을 덮어써버린다(모듈 docstring 참조).
            raise EmbeddedRelayError(f"인증서 파일 없음: {', '.join(missing)}")

        try:
            await async_process_requirements(self._hass, DOMAIN, [RELAY_REQUIREMENT])
            module = await self._hass.async_add_import_executor_job(importlib.import_module, "zigbang_relay")
            self._bin = str(await self._hass.async_add_executor_job(module.bin_path))
        except (HomeAssistantError, ImportError, OSError) as err:
            raise EmbeddedRelayError(f"{RELAY_REQUIREMENT} 설치/로드 실패(지원 안 되는 플랫폼?): {err}") from err

        await self._hass.async_add_executor_job(_prepare_dirs, self._base_dir)
        self.observer_port = _free_local_port()
        self._env = {"RELAY_CONFIG_JSON": self._config_json(str(module.RULES_DIR))}

        if failures := await self._spawn_and_check():
            await self._terminate()
            raise EmbeddedRelayError("relay 기동 실패: " + " / ".join(failures))

        self._supervisor = self._hass.async_create_background_task(self._supervise(), f"{DOMAIN}_embedded_relay")
        _LOGGER.info("내장 relay 시작(pid=%s, observer=127.0.0.1:%s)", self._proc.pid, self.observer_port)

    async def _spawn_and_check(self) -> list[str]:
        """relay 를 띄우고 기동 직후 _STARTUP_GRACE 동안 `✗ FAIL` 줄/조기종료를 모아 반환(빈 리스트=정상)."""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._bin,
                "env",
                env=self._env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:  # noexec 마운트 등
            return [f"실행 실패: {err}"]
        failures: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STARTUP_GRACE
        while (remaining := deadline - loop.time()) > 0:
            try:
                line = await asyncio.wait_for(self._proc.stderr.readline(), remaining)
            except asyncio.TimeoutError:
                break
            if not line:  # EOF = 프로세스 종료
                await self._proc.wait()
                failures.append(f"프로세스 종료(exit={self._proc.returncode})")
                break
            text = self._log_line(line)
            if "✗ FAIL" in text:
                failures.append(text)
        return failures

    def _log_line(self, raw: bytes) -> str:
        text = _LINE_PREFIX.sub("", raw.decode(errors="replace").rstrip())
        if "✗ FAIL" in text:
            _LOGGER.error("relay: %s", text)
        elif "⚠" in text:
            _LOGGER.warning("relay: %s", text)
        else:
            _LOGGER.debug("relay: %s", text)
        return text

    async def _supervise(self) -> None:
        """stderr 를 HA 로그로 넘기고, 죽으면 백오프 후 재기동(HA 가 멈추는 중이면 안 함)."""
        attempt = 0
        while not self._stopping:
            assert self._proc is not None
            while line := await self._proc.stderr.readline():
                self._log_line(line)
            await self._proc.wait()
            if self._stopping:
                return
            delay = _RESTART_BACKOFF[min(attempt, len(_RESTART_BACKOFF) - 1)]
            _LOGGER.error("내장 relay 종료됨(exit=%s), %s초 후 재기동", self._proc.returncode, delay)
            await asyncio.sleep(delay)
            if self._stopping:
                return
            if await self._spawn_and_check():
                # bind 실패여도 프로세스는 살아있으므로 직접 내려서 다음 루프에서 백오프 재시도.
                await self._terminate()
                attempt += 1
            else:
                attempt = 0

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), _STOP_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def stop(self) -> None:
        self._stopping = True
        await self._terminate()
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        _LOGGER.info("내장 relay 종료")


async def async_acquire(hass: HomeAssistant, entry_id: str, cert_name: str, cert_file: str, cert_key: str) -> int:
    """entry 를 내장 relay 사용자로 등록(필요하면 기동)하고 observer 포트를 반환."""
    shared = hass.data.setdefault(_DATA_KEY, {"lock": asyncio.Lock(), "relay": None, "users": set()})
    async with shared["lock"]:
        relay: EmbeddedRelay | None = shared["relay"]
        if relay is None:
            relay = EmbeddedRelay(hass, cert_name, cert_file, cert_key)
            await relay.start()
            shared["relay"] = relay

            async def _on_stop(_event: Event) -> None:
                # HA 종료시엔 config entry unload 가 안 불리므로 여기서 직접 내린다.
                if shared["relay"] is relay:
                    shared["relay"] = None
                    shared["users"].clear()
                    await relay.stop()

            shared["unsub_stop"] = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
        elif (cert_name, cert_file, cert_key) != (relay._cert_name, relay._cert_file, relay._cert_key):
            _LOGGER.warning("내장 relay 가 이미 다른 entry 설정으로 떠있어 이 entry 의 cert 설정은 무시됨")
        shared["users"].add(entry_id)
        return relay.observer_port


async def async_release(hass: HomeAssistant, entry_id: str) -> None:
    """entry 를 사용자에서 빼고, 아무도 안 쓰면 relay 를 내린다."""
    shared = hass.data.get(_DATA_KEY)
    if shared is None:
        return
    async with shared["lock"]:
        shared["users"].discard(entry_id)
        relay: EmbeddedRelay | None = shared["relay"]
        if relay is None or shared["users"]:
            return
        shared["relay"] = None
        with contextlib.suppress(ValueError):
            shared.pop("unsub_stop")()
        await relay.stop()
