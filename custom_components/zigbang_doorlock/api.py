"""직방 클라우드 REST — config_flow 에서 1회(로그인+도어락목록조회)만 씀.

이후 런타임 상태/제어는 relay_client.py(로컬 relay observer)가 전담 — 여기 있는 클라이언트는
async_setup_entry 이후 유지되지 않는다(iot_class: local_push).

엔드포인트/hashData 필드순서는 ../zigbang/src/lib.rs 의 `cloud::fetch`(Rust, provision용으로
이미 실기검증됨) 및 zigbang_doorlock_pyscript(zb_auth)와 동일 — 서버가 hashData 를
"dict 값 삽입순서 그대로 concat 후 SHA512" 로 검증하므로 순서를 반드시 지켜야 함.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime
from typing import Any

import aiohttp

BASE_URL = "https://iot.samsung-ihp.com:8088/openhome/"

_HEADERS_STATIC = {
    "Content-Type": "application/json",
    "acceptLanguage": "ko_KR",
    "User-Agent": "okhttp/4.2.1",
}


class ZigbangAuthError(Exception):
    """아이디/비밀번호/imei 오류(401 등)."""


class ZigbangConnectionError(Exception):
    """네트워크/서버 오류."""


class ZigbangCloudClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str, imei: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._imei = imei
        self._auth_token: str | None = None
        self._auth_code: str | None = None
        self._member_id: str | None = None

    @property
    def member_id(self) -> str | None:
        return self._member_id

    def _headers(self) -> dict[str, str]:
        headers = dict(_HEADERS_STATIC)
        headers["Authorization"] = f"CUL {self._auth_token}" if self._auth_token else "CUL "
        if self._auth_code:
            headers["AuthCode"] = self._auth_code
        return headers

    async def _get_appver(self) -> tuple[str, str]:
        params = {"createDate": _timestamp(), "hashData": "", "osTypeCd": "iOS "}
        data = await self._request("GET", "v20/appsetting/getappver", params=params)
        try:
            info = data["AppVersionList"][0]
            return info["osAppVer"], info["osTypeCd"]
        except (KeyError, IndexError, TypeError) as err:
            raise ZigbangConnectionError("getappver 응답 형식 이상") from err

    async def login(self) -> None:
        app_ver, os_type_cd = await self._get_appver()
        tz_hours = int(datetime.now().astimezone().utcoffset().total_seconds() // 3600)

        # 필드 순서 = 서버 hashData 검증 순서(위 모듈 docstring 참조), 임의 변경 금지.
        body: dict[str, Any] = {
            "apiVer": "v20",
            "authNumber": "",
            "countryCd": "KR",
            "locale": "ko_KR",
            "locationAgreeYn": "N",
            "mobileNum": "",
            "osVer": "13",
            "overwrite": True,
            "pushToken": "",
            "timeZone": tz_hours,
            "appVer": app_ver,
            "osTypeCd": os_type_cd,
            "createDate": _timestamp(),
            "loginId": self._username,
            "pwd": _sha512(self._password),
            "imei": self._imei,
        }
        body["hashData"] = _sha512("".join(str(v) for v in body.values()))

        try:
            async with self._session.put(BASE_URL + "v10/user/login", json=body, headers=self._headers()) as resp:
                if resp.status == 401:
                    raise ZigbangAuthError("인증 실패(401)")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise ZigbangConnectionError(str(err)) from err

        if not data.get("result"):
            raise ZigbangAuthError(str(data.get("message") or data))

        self._auth_token = data.get("authToken")
        self._auth_code = data.get("authCode")
        self._member_id = data.get("memberId")
        if not (self._auth_token and self._member_id):
            raise ZigbangAuthError("로그인 응답에 authToken/memberId 없음")

    async def fetch_doorlocks(self) -> list[dict[str, Any]]:
        if self._member_id is None:
            await self.login()

        params = {"createDate": _timestamp(), "favoriteYn": "A", "hashData": "", "memberId": self._member_id}
        data = await self._request("GET", "v20/doorlockctrl/membersdoorlocklist", params=params)

        locks = []
        for item in data.get("doorlockVOList", []):
            status = item.get("doorlockStatusVO", {}) or {}
            locks.append(
                {
                    "device_id": item.get("deviceId"),
                    "tp_id": item.get("tpId"),
                    "model": item.get("productId"),
                    "name": item.get("deviceNm") or "Zigbang 도어락",
                    "locked": status.get("locked"),
                    "battery_raw": status.get("battery"),
                }
            )
        return [lock for lock in locks if lock["tp_id"]]

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.request(method, BASE_URL + path, params=params, headers=self._headers()) as resp:
                if resp.status == 401:
                    raise ZigbangAuthError("인증 실패(401)")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise ZigbangConnectionError(str(err)) from err


def generate_imei() -> str:
    """Luhn 체크섬을 만족하는 15자리 랜덤 IMEI. config_flow 에서 1회 생성 후 entry.data 에 고정 저장 —
    로그인마다 바뀌면 클라우드 어뷰징 탐지에 걸릴 수 있어 재사용 필요(RUNBOOK.md 권고와 동일)."""
    digits = [random.randint(0, 9) for _ in range(14)]
    checksum = 0
    for i, digit in enumerate(digits):
        if (i + 1) % 2 == 0:
            doubled = digit * 2
            checksum += doubled if doubled < 10 else doubled - 9
        else:
            checksum += digit
    digits.append((10 - (checksum % 10)) % 10)
    return "".join(map(str, digits))


def _sha512(text: str) -> str:
    return hashlib.sha512(text.encode()).hexdigest()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")
