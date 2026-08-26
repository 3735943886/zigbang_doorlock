#!/usr/bin/env python3
"""직방 도어락 pin(등록된 카드/지문/임시키 등) 조회·삭제 CLI.

HA 통합구성요소와 완전히 독립적으로 실행 가능 — 표준 라이브러리 + mosquitto_pub/mosquitto_sub
(delete 시에만 필요, list는 REST만 사용)만 있으면 됨. 테스트하며 쌓인 임시키를 나중에 직접
정리하고 싶을 때 쓰는 용도.

사용례:
  python3 helper/manage_pins.py --id ID list
  python3 helper/manage_pins.py --id ID delete --pin-id 5 --relay-host 10.10.1.5
  python3 helper/manage_pins.py --id ID delete --pin-id 5 --relay-host 10.10.1.5 --sid CB00000000 --yes

비밀번호는 인자로 주면 쉘 히스토리/프로세스 목록에 남으니, 인자를 생략하면 프롬프트로 물어봄.

IMEI는 계정당 고정돼야 함 — 로그인마다 바뀌면 클라우드가 "새 기기"로 인식해서 그 기기용
NFC pin을 자동 발급해버림(실측 확인, 2026-08-21: list만 반복 실행해도 매번 pin이 하나씩 쌓임).
그래서 --imei를 생략하면 최초 실행 시 1회 생성해서 이 스크립트 옆의 .manage_pins_imei.json 에
로그인ID별로 캐싱해두고 이후 실행에서 재사용한다.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_URL = "https://iot.samsung-ihp.com:8088/openhome/"
PIN_TYPES = "MST,NFC,NUM,RFC,FGP,SDK,RMC,VCE,BLE,FCE"
IMEI_CACHE_PATH = Path(__file__).parent / ".manage_pins_imei.json"


def generate_imei() -> str:
    """Luhn 체크섬을 만족하는 15자리 랜덤 IMEI(custom_components/.../api.py의 generate_imei와 동일 로직 —
    이 스크립트는 HA와 독립적으로 돌아가야 해서 import 대신 복제)."""
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


def cached_imei(login_id: str) -> str:
    cache: dict[str, str] = {}
    if IMEI_CACHE_PATH.exists():
        try:
            cache = json.loads(IMEI_CACHE_PATH.read_text())
        except (ValueError, OSError):
            cache = {}
    if login_id in cache:
        return cache[login_id]
    imei = generate_imei()
    cache[login_id] = imei
    IMEI_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    print(f"(참고: 이 계정용 IMEI를 새로 생성해 {IMEI_CACHE_PATH} 에 저장했습니다. "
          f"다음 실행부터는 이 값을 재사용합니다.)", file=sys.stderr)
    return imei


def sha512(s: str) -> str:
    return hashlib.sha512(s.encode()).hexdigest()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def request(method: str, path: str, params: dict | None = None, body: dict | None = None,
            auth: str = "CUL ", auth_code: str | None = None) -> dict:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/json", "acceptLanguage": "ko_KR",
        "User-Agent": "okhttp/4.2.1", "Authorization": auth,
    }
    if auth_code:
        headers["AuthCode"] = auth_code
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def login(username: str, password: str, imei: str) -> tuple[str, str, str]:
    appver = request("GET", "v20/appsetting/getappver",
                      params={"createDate": timestamp(), "hashData": "", "osTypeCd": "iOS "})
    info = appver["AppVersionList"][0]
    app_ver, os_type_cd = info["osAppVer"], info["osTypeCd"]
    tz = int(datetime.now().astimezone().utcoffset().total_seconds() // 3600)
    body = {
        "apiVer": "v20", "authNumber": "", "countryCd": "KR", "locale": "ko_KR",
        "locationAgreeYn": "N", "mobileNum": "", "osVer": "13", "overwrite": True,
        "pushToken": "", "timeZone": tz, "appVer": app_ver, "osTypeCd": os_type_cd,
        "createDate": timestamp(), "loginId": username, "pwd": sha512(password), "imei": imei,
    }
    body["hashData"] = sha512("".join(str(v) for v in body.values()))
    resp = request("PUT", "v10/user/login", body=body)
    if not resp.get("result"):
        sys.exit(f"로그인 실패: {resp.get('message') or resp}")
    return resp["memberId"], resp["authToken"], resp["authCode"]


def fetch_doorlocks(member_id: str, auth_header: str, auth_code: str) -> list[dict]:
    resp = request("GET", "v20/doorlockctrl/membersdoorlocklist",
                    params={"createDate": timestamp(), "favoriteYn": "A", "hashData": "", "memberId": member_id},
                    auth=auth_header, auth_code=auth_code)
    return resp.get("doorlockVOList", [])


def fetch_pin_infos(member_id: str, device_id: str, auth_header: str, auth_code: str) -> list[dict]:
    resp = request("GET", "v20/doorlockctrl/getdoorkeys",
                    params={"memberId": member_id, "deviceId": device_id, "pinTypes": PIN_TYPES,
                            "createDate": timestamp(), "hashData": ""},
                    auth=auth_header, auth_code=auth_code)
    return resp.get("pinInfos", [])


def mask(value: Any) -> str:
    if not value:
        return str(value)
    s = str(value)
    return f"{s[:4]}***{s[-2:]}" if len(s) > 8 else "***"


def pick_device(username: str, password: str, imei: str) -> tuple[str, str, str, dict]:
    member_id, auth_token, auth_code = login(username, password, imei)
    auth_header = f"CUL {auth_token}"
    locks = fetch_doorlocks(member_id, auth_header, auth_code)
    if not locks:
        sys.exit("등록된 도어락이 없습니다.")
    if len(locks) == 1:
        return member_id, auth_header, auth_code, locks[0]

    print("여러 도어락이 있습니다:")
    for i, lk in enumerate(locks):
        print(f"  [{i}] {lk.get('deviceNm')} ({lk.get('tpId')})")
    idx = int(input("번호 선택: "))
    return member_id, auth_header, auth_code, locks[idx]


def cmd_status(args: argparse.Namespace) -> None:
    """membersdoorlocklist 응답의 도어락 항목을 가공 없이 그대로 덤프 — api.py의 fetch_doorlocks는
    doorlockStatusVO에서 locked/battery만 뽑아쓰는데, dummyMode/mode/useMagicNumber/use2wayAuth
    같은 필드가 여기 원본에 이미 들어있는지 확인하는 용도(2026-08-26 논의: 스위치 초기상태가
    relay 트래픽 잡히기 전까진 계속 unknown인 문제, REST로 시딩 가능한지 확인)."""
    member_id, auth_header, auth_code, lock = pick_device(args.id, args.password, args.imei)
    print(json.dumps(lock, indent=2, ensure_ascii=False))


def cmd_list(args: argparse.Namespace) -> None:
    member_id, auth_header, auth_code, lock = pick_device(args.id, args.password, args.imei)
    pins = fetch_pin_infos(member_id, lock["deviceId"], auth_header, auth_code)
    print(f"\n{lock.get('deviceNm')} ({lock.get('tpId')}) — 등록된 pin {len(pins)}건:\n")
    for p in pins:
        print(
            f"  pinId={p['pinId']:<4} type={p['pinType']:<5} "
            f"name={(p.get('pinName') or ''):<20} regDate={p.get('pinRegDate') or ''}  pin={mask(p.get('pin'))}"
        )


def sniff_sid(relay_host: str, relay_port: int, tp_id: str, timeout: int) -> str | None:
    print(f"relay observer({relay_host}:{relay_port})에서 sId 관측 시도(최대 {timeout}초, 하트비트 주기가"
          f" 길어서 못 잡을 수 있음 — 그때는 --sid로 직접 지정)...")
    try:
        proc = subprocess.run(
            ["mosquitto_sub", "-h", relay_host, "-p", str(relay_port), "-t", "#", "-v", "-W", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        output = proc.stdout
    except subprocess.TimeoutExpired as err:
        output = (err.stdout or b"").decode() if isinstance(err.stdout, bytes) else (err.stdout or "")

    for line in output.splitlines():
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            payload = json.loads(line[idx:])
        except ValueError:
            continue
        if payload.get("tpId") == tp_id and payload.get("sId"):
            return payload["sId"]
    return None


def cmd_delete(args: argparse.Namespace) -> None:
    member_id, auth_header, auth_code, lock = pick_device(args.id, args.password, args.imei)
    tp_id = lock["tpId"]
    pins = fetch_pin_infos(member_id, lock["deviceId"], auth_header, auth_code)
    target = next((p for p in pins if p["pinId"] == args.pin_id), None)
    if not target:
        sys.exit(f"pinId={args.pin_id} 를 찾을 수 없습니다. 'list'로 먼저 확인하세요.")

    print(f"삭제 대상: pinId={target['pinId']} type={target['pinType']} name={target.get('pinName')!r}")
    if not args.yes and input("정말 삭제할까요? (yes 입력): ").strip().lower() != "yes":
        print("취소됨.")
        return

    sid = args.sid or sniff_sid(args.relay_host, args.relay_port, tp_id, args.sniff_timeout)
    if not sid:
        sys.exit(
            "sId를 못 얻었습니다. --sid로 직접 지정하거나(이전 관측값 재사용 가능 — 같은 계정/락이면 "
            "보통 고정값), 도어락이 트래픽을 보낼 때 다시 시도하세요."
        )

    # 실캡처 기준 정확한 func:420 payload(authToken 필드 자체 없음, resCode:"200") —
    # zigbang_doorlock/custom_components/zigbang_doorlock/protocol.py 의 build_register_key/
    # build_trigger_unlock 과 형제뻘 payload. 이 스크립트는 HA와 독립적이라 별도로 인라인 구성.
    payload = {
        "version": "1.0", "msgType": "Q", "funcType": "030",
        "sId": sid, "tpId": tp_id, "tId": tp_id,
        "msgCode": "MSGBA0300001", "msgId": str(uuid.uuid4()), "msgDate": int(time.time() * 1000),
        "resCode": "200", "resMsg": "", "dataFormat": "application/json",
        "severity": "0", "encType": "0",
        "data": {"func": 420, "arg": {"pin": target["pin"]}},
    }
    topic = f"ocp/{sid}/{tp_id}"
    subprocess.run(
        ["mosquitto_pub", "-h", args.relay_host, "-p", str(args.relay_port), "-t", topic, "-m", json.dumps(payload)],
        check=True, timeout=10,
    )
    print("삭제 명령 전송 완료. 5초 후 재확인...")
    time.sleep(5)

    after = fetch_pin_infos(member_id, lock["deviceId"], auth_header, auth_code)
    still_there = any(p["pinId"] == args.pin_id for p in after)
    print("결과:", "아직 남아있음(실패했거나 반영 지연 — 잠시 후 list로 재확인)" if still_there else "삭제 확인됨")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", required=True, help="직방 로그인 ID")
    parser.add_argument("--password", help="직방 비밀번호(생략시 프롬프트로 입력)")
    parser.add_argument("--imei", default=None,
                        help="생략시 계정별로 1회 생성 후 .manage_pins_imei.json 에 캐싱해 재사용 "
                             "(매번 새 값을 쓰면 클라우드가 새 기기로 인식해 pin을 자동 발급함)")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="등록된 pin 목록 조회(REST만, relay 불필요)")
    sub.add_parser("status", help="membersdoorlocklist 원본 응답 그대로 덤프(REST만, relay 불필요)")

    d = sub.add_parser("delete", help="지정한 pinId 삭제(REST 조회 + relay observer로 injection)")
    d.add_argument("--pin-id", type=int, required=True)
    d.add_argument("--relay-host", required=True)
    d.add_argument("--relay-port", type=int, default=9883)
    d.add_argument("--sid", default=None, help="생략시 relay에서 잠깐 관측 시도")
    d.add_argument("--sniff-timeout", type=int, default=20)
    d.add_argument("--yes", action="store_true", help="확인 프롬프트 생략")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.password:
        args.password = getpass.getpass("직방 비밀번호: ")
    if not args.imei:
        args.imei = cached_imei(args.id)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "delete":
        cmd_delete(args)


if __name__ == "__main__":
    main()
