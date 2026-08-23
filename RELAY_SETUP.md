# relay(로컬 중계 서버) 띄우기 — 처음부터 끝까지

이 저장소(HA 커스텀 컴포넌트)는 도어락과 직접 통신하지 않습니다 — 락↔클라우드 트래픽을
로컬에서 중계하는 별도 데몬(`zigbang-relay`)이 먼저 떠있어야 합니다. 이 문서는 **이 저장소만
보고** 그 relay를 띄우는 법부터 도어락 재프로비저닝, 커스텀 컴포넌트 설치까지 끝까지 설명합니다.

relay는 **Docker 컨테이너** 또는 **HA App(Add-on)**, 둘 중 편한 쪽으로 띄우면 됩니다(같은
사전빌드 이미지를 씀 — 기능 차이 없음):

| | Docker | HA App(Add-on) |
|---|---|---|
| 어디에 뜨는가 | 아무 리눅스 머신(HA와 같은 머신 아니어도 됨) | HA(HAOS/Supervised) 안 |
| 설치 방법 | `docker run` | Supervisor → 앱 스토어 → 저장소 추가 |
| 설정 파일 위치 | 원하는 호스트 디렉터리 | app 데이터 폴더(`/data`) |
| 인증서 준비 | certbot 등으로 직접 발급 | 커뮤니티 "Let's Encrypt" app 연동 권장(HAOS 표준 `/ssl` 경로 자동 인식) |
| observer(9883) 노출 | 방화벽으로 직접 통제해야 함 | 기본적으로 host에 안 열림(HA 내부망에서만) |

> relay/provision 바이너리와 Docker 이미지는 [`zigbang_relay`](https://github.com/3735943886/zigbang_relay)
> 저장소에서 배포합니다(공개 저장소, 로그인 없이 [Releases](https://github.com/3735943886/zigbang_relay/releases)에서
> 바로 다운로드 가능). Docker 이미지는 [Docker Hub](https://hub.docker.com/r/3735943886/zigbang-relay)에서
> pull하고, 컨테이너를 띄우는 데 필요한 설정 파일(`rules/`, `relay.toml.example`)은 이 저장소의
> [`docker/`](docker/)에 그대로 들어있습니다 — 이 문서 + `docker/`만 있으면 relay 배포는 완결됩니다.

## 전체 흐름

1. [도메인 준비](#1-도메인-준비)
2. [TLS 인증서 발급](#2-tls-인증서-발급-lets-encrypt)
3. [그 도메인이 실제로 relay까지 접속되는지 확인](#3-도메인이-relay까지-실제로-접속되는지-확인)
4. [(대안) 이미 있는 리버스프록시 재활용](#4-대안-이미-리버스프록시가-있는-ha-환경이라면)
5. [relay 띄우기 — Docker 또는 Add-on](#5-relay-띄우기--docker-또는-add-on)
6. [도어락을 재연동 모드로 리셋](#6-도어락을-재연동-모드로-리셋)
7. [도어락을 이 relay로 재프로비저닝](#7-도어락을-이-relay로-재프로비저닝)
8. [커스텀 컴포넌트 설치](#8-커스텀-컴포넌트-설치)

## 1. 도메인 준비

relay가 스스로를 증명할 도메인 하나가 필요합니다. 유료 도메인이 없어도 무료 DDNS로 충분합니다:

- [DuckDNS](https://www.duckdns.org/) — 가장 간단, 구글/깃허브 계정으로 바로 서브도메인 발급
- [afraid.org (FreeDNS)](https://freedns.afraid.org/) — 선택지 다양
- 이미 갖고 있는 개인 도메인의 서브도메인(예: `relay.내도메인.com`)도 그대로 사용 가능

**A레코드로 relay가 뜨는 서버의 IP를 직접 지정**하세요(사설 IP도 가능 — 락은 그 IP로 직접
접속을 시도합니다). Cloudflare처럼 프록시를 끼우는 DNS는 CNAME 방식이면 2단계의 Let's
Encrypt HTTP-01 발급이 막힐 수 있어 권장하지 않습니다("DNS only"/회색 구름 모드로 두세요).

## 2. TLS 인증서 발급 (Let's Encrypt)

도어락은 **공인 CA가 발급한 인증서만 수락**합니다 — 자체서명 인증서는 무조건 거부되고,
**호스트명도 검사**하므로 여기서 발급받은 도메인이 7단계에서 락에 주입하는 도메인과
정확히 일치해야 합니다.

```bash
sudo apt install certbot   # 또는 배포판에 맞는 방법
sudo certbot certonly --standalone -d relay.example.com   # 실제 도메인으로
```

발급된 인증서는 `/etc/letsencrypt/live/relay.example.com/`에 생깁니다 — 5단계에서 이 파일을
가져다 씁니다. **자동 갱신**은 5단계 이후에 설정하는 걸 권장합니다(대상 경로가 먼저 있어야
갱신 훅이 그 자리에 복사할 수 있음).

> **HA App(Add-on)으로 띄울 거라면 이 단계 대신 커뮤니티 "Let's Encrypt" app 사용을
> 권장합니다** — 아래 [5-B](#5-b-ha-appadd-on으로-띄우기)에서 설명. HAOS엔 보통 certbot을
> 직접 돌릴 셸이 없기도 하고, app 쪽이 HA 표준 경로(`/ssl/`)에 자동으로 인증서를 놓아줘서
> 이 relay app이 별도 설정 없이 바로 읽어갑니다.

## 3. 도메인이 relay까지 실제로 접속되는지 확인

relay를 집/인트라넷 안에 두면, 도메인이 가리키는 공인 IP로 나갔다가 다시 안으로 들어와야
하는 경우가 생깁니다(포트포워딩은 해놨지만 내부망 안에서 그 공인IP:포트로 접속하면 안
들어와지는 라우터가 많음) — 이를 **NAT 루프백(헤어핀 NAT)**이라고 하며, 지원 안 하는
공유기가 흔합니다.

- 확인법: 락과 같은 내부망의 다른 기기에서 `openssl s_client -connect relay.example.com:18883`
  등으로 접속이 되는지 테스트
- 안 되면: 1단계의 A레코드를 **공인 IP 대신 relay가 뜬 서버의 사설 IP로 직접 지정**하거나,
  라우터/내부 DNS에서 그 도메인만 사설 IP로 오버라이드(로컬 DNS 오버라이드)
- 락은 어차피 그 도메인을 DNS로 풀어서 나온 IP로 접속하는 것뿐이라, 내부망에서 사설 IP로
  바로 풀리게만 만들면 공인 IP 경유 자체가 필요 없어집니다(락과 relay가 같은 내부망이라면
  이 방법이 가장 간단하고 확실함)

## 4. (대안) 이미 리버스프록시가 있는 HA 환경이라면

Nginx Proxy Manager, Traefik, Caddy 등으로 이미 HA를 외부 도메인에 물려두고 인증서도
관리하고 있다면, 새 도메인/인증서를 따로 준비하지 않고 **그 도메인에 relay의 포트만 얹어서**
재활용할 수 있습니다(TLS 종료를 리버스프록시가 대신 하고, relay의 `18883`/`5683`으로 스트림
전달). 이 경우 1~2단계는 건너뛰고 5단계로 바로 가면 됩니다.

**단, 보안 주의**: 이 relay는 원래 인터넷에 노출할 목적이 아니라 "도어락 하나가 접속해오는
전용 엔드포인트"입니다. 기존 HA 리버스프록시에 얹으면 그 프록시가 이미 노출하고 있는
공격 표면에 도어락 제어 경로까지 얹는 셈이라, 최소한:
- `observer`(9883, 평문 무인증 포트)는 **절대** 리버스프록시나 인터넷에 노출하지 말 것 —
  로컬신뢰망에서만 접근 가능하게 방화벽으로 막아야 합니다(app으로 띄우면 기본적으로
  host에 안 열려서 이 항목이 자동으로 해결됨).
- `18883`(MQTTS)/`5683`(UDP) 스트림 전달용 포트만 최소한으로 열고, 가능하면 소스 IP 제한
  (도어락이 실제로 나가는 IP만 허용) 등 프록시단 접근제어를 추가로 고려하세요.

## 5. relay 띄우기 — Docker 또는 App(Add-on)

### 5-A. Docker로 띄우기

#### 5-A-1. 디렉터리 준비

호스트에 디렉터리 하나(`/opt/zigbang-relay` 등, 경로는 아무 데나 상관없음)를 만들고 아래
레이아웃을 준비합니다 — **이 디렉터리 전체가 컨테이너의 `/data`에 통째로 마운트**됩니다:

```bash
mkdir -p /opt/zigbang-relay/certs /opt/zigbang-relay/state /opt/zigbang-relay/logs

# 이 저장소에 이미 들어있는 설정 파일들을 복사
cp -r docker/rules /opt/zigbang-relay/rules
cp docker/relay.toml.example /opt/zigbang-relay/relay.toml

# 2단계에서 발급받은 인증서 복사
sudo install -m 0644 /etc/letsencrypt/live/relay.example.com/fullchain.pem /opt/zigbang-relay/certs/
sudo install -m 0640 /etc/letsencrypt/live/relay.example.com/privkey.pem   /opt/zigbang-relay/certs/
```

`/opt/zigbang-relay/relay.toml`을 열어 `cert_name`(← 실제 발급받은 도메인)과 두 `upstream`
(실제 직방 클라우드 브로커 주소)을 채워넣습니다. `rules/`는 그대로 두면 됩니다 — 락↔클라우드
메시지에 어떻게 응답할지 정하는 [Rhai](https://rhai.rs) 스크립트 디렉터리로(안의 `*.rhai`를
파일명 알파벳순으로 전부 불러 씀), **mtime 감시로 핫리로드**되므로 나중에 갱신해도 컨테이너
재시작이 필요 없습니다.

| 경로 | 무엇인가 | 어떻게 생기나 |
|---|---|---|
| `relay.toml` | 설정 파일 | `docker/relay.toml.example` 복사 후 값 채움 |
| `rules/*.rhai` | 메시지 응답 규칙(Rhai 스크립트) | `docker/rules/` 그대로 복사 |
| `certs/fullchain.pem`, `certs/privkey.pem` | TLS 인증서 | 2단계에서 발급 |
| `state/replay.json` | 재생 캐시(장애 대응용) | 자동 생성, 미리 안 만들어도 됨 |
| `logs/` | relay 로그 | 자동 생성 |

#### 5-A-2. 컨테이너 실행

이미지는 직접 빌드할 필요 없이 Docker Hub에서 pull합니다(멀티아치: amd64+arm64):

```bash
docker pull 3735943886/zigbang-relay:stable

docker run -d --restart=always --name zigbang-relay \
  --user $(id -u):$(id -g) \
  -p 18883:18883 -p 5683:5683/udp -p 9883:9883 \
  -v /opt/zigbang-relay:/data \
  3735943886/zigbang-relay:stable
```

- `--user $(id -u):$(id -g)`로 호스트에서 `/opt/zigbang-relay`를 소유한 유저 그대로 넘기면
  이미지 기본 UID(10001)와 무관하게 쓰기(state/log)가 바로 됩니다.
- `-p 9883`(평문 관찰자 포트)은 **로컬신뢰망에서만** 접근 가능하게 방화벽으로 반드시 막으세요
  (이 저장소의 통합구성요소도, `helper/manage_pins.py`도 이 포트로 접속합니다 — 인터넷엔
  절대 노출 금지).

확인:
```bash
docker logs -f zigbang-relay
```

**인증서 자동 갱신** — `/etc/letsencrypt/renewal-hooks/deploy/zigbang-relay.sh`:
```bash
#!/usr/bin/env bash
set -e
D=relay.example.com   # ← 실제 도메인
if [[ "$RENEWED_LINEAGE" == *"/$D" ]]; then
  install -m 0644 "$RENEWED_LINEAGE/fullchain.pem" /opt/zigbang-relay/certs/fullchain.pem
  install -m 0640 "$RENEWED_LINEAGE/privkey.pem"   /opt/zigbang-relay/certs/privkey.pem
fi
```
`chmod +x` 해두면 certbot 갱신마다 자동 실행됩니다. relay는 cert 파일 mtime을 감시해서
**컨테이너 재시작 없이** 새 인증서를 바로 반영하므로 이 훅에 `docker restart`를 넣을 필요조차
없습니다.

### 5-B. HA App(Add-on)으로 띄우기

HAOS/Supervised 환경이라면 별도 서버 없이 HA 안에서 바로 띄울 수 있습니다.

#### 5-B-1. 저장소 추가 + 설치

HA → 설정 → 애드온 → 애드온 스토어 → 우측 상단 ⋮ → 저장소 → 아래 URL 추가:
```
https://github.com/3735943886/zigbang_relay
```
"Zigbang Doorlock Relay" 검색 후 설치.

#### 5-B-2. rules/ 배치

app 데이터 폴더(`/data` — Samba 공유나 File editor app으로 접근)에 이 저장소의
`docker/rules/`를 그대로 복사합니다:
```
/data/rules/  ← docker/rules/와 동일 내용
```
**mtime 핫리로드**라 나중에 갱신해도 app 재시작이 필요 없습니다.

#### 5-B-3. 인증서

**커뮤니티 "Let's Encrypt" app을 같이 설치**하는 걸 권장합니다 — 그 app이 발급한
인증서는 HA 표준 공유경로(`/ssl/fullchain.pem`+`/ssl/privkey.pem`)에 떨어지고, 이 relay
app은 그 경로를 읽기전용으로 이미 기본 설정돼있어(`cert_file`/`cert_key` 기본값)
**추가 설정 없이 바로 반영**됩니다. 갱신도 mtime 핫리로드라 app 재시작 불필요.

HAOS가 아니거나 `/ssl`을 안 쓰고 싶으면: app "구성" 탭에서 `cert_file`/`cert_key`를 원하는
경로(예: `/data/certs/fullchain.pem`)로 바꾸고 2단계처럼 직접 발급한 인증서를 그 경로에 넣어도
됩니다.

#### 5-B-4. 옵션 설정

app "구성" 탭에서 아래 값을 실제 값으로 채웁니다:
- `cert_name` ← 1~2단계에서 준비한 실제 도메인
- `route` 배열의 두 `upstream`(mqtts/holepunch) ← 실제 직방 클라우드 브로커 주소

나머지는 기본값으로 충분합니다. 저장 후 app 재시작.

#### 5-B-5. 확인

"로그" 탭에서 `relay 시작 — routes=2 ...`가 보이면 정상입니다.

> **observer(9883)는 기본적으로 host에 안 열립니다** — HA 내부 도커망에서만 접근 가능(의도적
> 보안 설계). 이 저장소의 커스텀 컴포넌트도 같은 HA 안에서 돌아가니 대부분은 문제 없지만,
> `helper/manage_pins.py` 등 **HA 바깥에서** 9883에 직접 붙어야 하는 도구를 쓰려면 이 add-on을
> 포크해서 `config.yaml`의 `ports`에 `9883/tcp`를 추가해야 합니다(로컬신뢰망 밖으로는 절대
> 노출 금지 — [zigbang_relay의 config.yaml](https://github.com/3735943886/zigbang_relay/blob/main/zigbang-relay-addon/config.yaml)
> 주석 참조).

## 6. 도어락을 재연동 모드로 리셋

relay가 5단계까지 실제로 응답 가능한 상태로 떠있는지 확인한 뒤 이 단계를 시작하세요 — 리셋한
락은 SoftAP를 띄운 채 재프로비저닝(7단계)을 기다리는 상태가 되므로, 곧바로 이어서 진행합니다.

(SHP-DP960SG 기준) 문을 **열어둔 상태**에서 `reg` 버튼을 **5초간** 길게 누릅니다. 락이
음성/비프로 비밀번호 입력을 요구하면, 등록된 비밀번호 + `#`을 눌러 확인합니다 — 이걸로
기존 클라우드 연동이 초기화되고 락이 SoftAP(자체 WiFi)를 다시 띄워서 7단계의 재프로비저닝을
받을 준비 상태가 됩니다.

> 다른 모델은 리셋 절차(버튼 위치/길게 누르는 시간)가 다를 수 있습니다 — 제조사 설명서를
> 우선 확인하세요. 이 단계는 **완전 공장초기화가 아니라 "연동 정보만" 초기화**하는 절차라
> 등록된 카드/지문/비밀번호 등은 보존됩니다(실기 확인).

## 7. 도어락을 이 relay로 재프로비저닝

### 7-1. 하드웨어 준비물

**WiFi 연결이 가능한 포터블 기기 아무거나** — 노트북(Windows/Linux/macOS)이면 충분하고, PC가
마땅치 않으면 **아이폰 + [iSH](https://ish.app/)**(App Store에서 받는 iOS용 리눅스 터미널
에뮬레이터, x86 32비트로 동작)로도 됩니다 — 아래 표의 `linux-i686` 빌드가 바로 이 용도입니다.
어차피 SoftAP에 잠깐 붙어서 명령 한 번 실행하는 게 전부라, 상시로 쓸 장비일 필요는 없습니다.

### 7-2. `provision` 바이너리 받기

그 기기에 [zigbang_relay Releases](https://github.com/3735943886/zigbang_relay/releases)에서
자신의 환경에 맞는 파일을 받습니다(로그인 불필요, 공개 저장소):

| 환경 | 파일 |
|---|---|
| Linux x86-64(노트북 등) | `provision-linux-amd64` |
| Linux ARM64 | `provision-linux-arm64` |
| iPhone(iSH) / 32비트 x86 리눅스 | `provision-linux-i686` |
| Windows x86-64 | `provision-windows-amd64.exe` |

Linux/macOS/iSH면 실행권한을 줍니다: `chmod +x provision-linux-amd64`.

### 7-3. 실행 순서 이해하기 — 먼저 인터넷, 도중에 SoftAP로 전환

`provision` 한 번 실행이 내부적으로 두 단계로 나뉘고, **그 사이에 네트워크를 바꿔줘야** 합니다:

1. **온라인 상태(지금 쓰던 집 WiFi/인터넷)에서 시작** — 여기서 계정 로그인(memberId/authCode/
   serviceUrl 취득)과, 주입할 relay(`--service-url`)가 실제로 도달 가능하고 cert가 유효한지
   사전점검을 합니다. **아직 SoftAP로 안 바꾼 상태**라야 이 단계가 됩니다(사전점검은 실패해도
   경고만 하고 진행은 계속함).
2. 그 다음 화면에 `도어락 SoftAP 대기 중 → 10.0.0.1:5000 (연결되면 자동 진행, 지금 SoftAP로
   갈아끼우세요)`가 뜨고, 2초 간격으로 연결을 계속 시도하며 **무제한 대기**합니다 — **이때
   커미셔닝 기기의 WiFi를 락의 SoftAP(6단계에서 리셋한 락이 방송하는, 보통 모델명이 포함된
   SSID)로 전환**하세요. 연결되면 자동으로 핸드셰이크→등록 패킷 주입으로 넘어갑니다.

즉 순서는 **"인터넷 연결 상태로 provision 실행 → 대기 메시지 보고 그때 SoftAP로 전환"**이지,
SoftAP에 먼저 붙여놓고 실행하는 게 아닙니다(SoftAP는 보통 인터넷 업링크가 없어서, 먼저
붙어버리면 위 1번의 로그인/사전점검 자체가 실패합니다). 완료(성공/실패 불문) 후엔 커미셔닝
기기를 다시 원래 WiFi로 돌려놓으면 됩니다 — **재실행할 때마다 이 온라인→SoftAP 전환을 매번
반복**해야 합니다(예: 아래 드라이런 한 번, 본 실행 한 번 하면 두 번 다 이 전환을 거칩니다).

등록 패킷엔 집 WiFi SSID/비번 + 우리 relay의 `service_url`/`service_port`(+ 2차 UDP용
`service_url2`/`service_port2`)가 담겨서 락에 주입됩니다. 락이 받아들이면 그때부터 SoftAP를
내리고 **집 WiFi로 붙은 뒤 우리가 주입한 주소로 접속을 시작**합니다 — `--service-url`에 넣은
값이 곧 락이 평생(다음 재프로비저닝 전까지) 접속하려 드는 주소가 되므로, 오타 없이 **relay가
실제로 응답하는 도메인**(1~2단계에서 준비한 것)과 정확히 같은지 실행 전에 한 번 더 확인하세요.

> 락이 "신규 등록 가능 상태"가 아니면(핸드셰이크 응답이 신규 값이 아닌 경우) provision이
> 자체적으로 "공장초기화(페어링모드) 후 재시도" 에러를 내고 멈춥니다 — 6단계 리셋을 제대로
> 안 했거나 이미 한 번 성공적으로 재프로비저닝된 락에 또 시도하면 이 에러가 뜹니다.

### 7-4. 실행

먼저 **드라이런으로 확인**(락 상태를 안 바꿈, 핸드셰이크만 확인 — 7-3의 온라인→SoftAP 전환은
이것도 동일하게 거칩니다):
```bash
./provision-linux-amd64 --handshake-only --service-url relay.example.com --service-port 18883
```
`OK`류 응답이 보이면 SoftAP 연결과 락 자체는 정상입니다. 다 끝나면 일단 원래 WiFi로 돌아왔다가,
이상 없으면 본 커미셔닝을 실행합니다(다시 온라인 상태로 시작):
```bash
./provision-linux-amd64 \
  --login-id 직방계정ID --login-pass 비번 \
  --service-url relay.example.com --service-port 18883 \
  --service-url2 relay.example.com --service-port2 5683 \
  --home-ssid 우리집WiFi --home-pass WiFi비번
```

인자를 다 채우면 프롬프트 없이 바로 진행되고, 빠진 값(로그인 수단/집 WiFi)이 있으면 그것만
대화형으로 물어봅니다 — `./provision-linux-amd64`만 실행해도 됩니다.

**자주 쓰는 옵션**(전체 목록은 `./provision-linux-amd64 --help`):

| 옵션 | 의미 |
|---|---|
| `--service-url`, `--service-port` | ★ 주입할 relay 주소(MQTTS). **1~2단계에서 준비한 도메인** |
| `--service-url2`, `--service-port2` | ★ 주입할 relay 주소(UDP 홀펀치, 보통 위와 같은 도메인 + `5683`) |
| `--login-id`, `--login-pass` | 직방 계정으로 로그인해서 memberId/authCode/serviceUrl을 자동 취득(둘 다 지정해야 함) |
| `--home-ssid`, `--home-pass` | 락이 붙을 집 WiFi(미지정시 대화형으로 물어봄) |
| `--auth-mode` | 집 WiFi의 인증방식 코드. 기본값 `1`(WPA류, 대부분의 가정용 WiFi)이면 충분 — `0`으로 등록하면 SoftAP 프로토콜 자체는 통과해도 실제 집 WiFi 접속엔 조용히 실패하니 건드리지 말 것 |
| `--name` | 락 표시이름(기본 `ZigbangLock`) |
| `--handshake-only` | 락 상태를 바꾸지 않고 핸드셰이크 응답만 확인(위 드라이런) |
| `--skip-preflight` | 7-3의 온라인 사전점검(relay 도달성+cert)을 생략. 기본은 **점검함** |
| `--device-addr` | SoftAP 게이트웨이 주소. 기본 `10.0.0.1:5000`(대부분의 기기가 이 값) |
| `--imei` | 로그인시 쓸 IMEI(미지정시 랜덤 생성 — 이건 최초 1회성 커미셔닝이라 매번 로그인하는 `manage_pins.py`류와 달리 재사용 안 해도 무방) |
| `--legacy` | 구형 AES 키("Samsung_DoorLock") 사용. 기본은 신형("Zigbang_DoorLock", 2024) — 락이 오래된 모델이고 기본으로 실패하면 시도 |
| `--force-registered` | 핸드셰이크 응답의 msgId가 기대값(100)과 달라도 강행. 기본은 안전하게 중단(7-3의 "공장초기화 후 재시도" 에러) |

### 7-5. 성공/실패 확인

성공하면 콘솔에 `커미셔닝 완료 — 로그/캡처 파일 확인, 릴레이 로그에서 도어락 접속 관찰`이
찍힙니다. 그 직후 5단계에서 띄운 relay에 락 트래픽이 실제로 들어오는지 확인하세요 — 아무거나
편한 쪽으로:
```bash
docker logs -f zigbang-relay          # Docker(5-A)
# HA → 애드온 → Zigbang Doorlock Relay → 로그 탭  (App, 5-B)

# 또는 observer 포트를 직접 tap(로컬신뢰망에서, 9883이 열려있는 경우):
mosquitto_sub -h <relay-host> -p 9883 -t '#' -v
```
락 트래픽이 여기 찍히기 시작하면(보통 몇 초~하트비트 주기 이내) 완전히 성공한 것입니다 —
안 찍히면 락이 집 WiFi 접속 자체에 실패했거나(`--auth-mode` 확인), 주입한 도메인에 relay가
실제로 응답 못하는 상태(3단계의 NAT 루프백 문제 등)일 수 있습니다.

실패하면 어느 단계(`resolve`/`run`)에서 멈췄는지와 에러가 함께 출력되고, 실행 디렉터리에
`.log`/`.capture` 파일이 남습니다(`.capture`는 원시 바이트라 오프라인 분석용 — 문제 재현/문의할 때
같이 보면 도움이 됨). 재시도할 땐 **6단계(리셋)부터 다시** 해야 합니다 — 한 번 SoftAP를 벗어난
락은 다시 커미셔닝 모드로 되돌리지 않으면 provision이 아예 안 붙습니다.

## 8. 커스텀 컴포넌트 설치

relay가 락 트래픽을 실제로 받고 있는 게 확인되면, 이제 이 저장소(HA 커스텀 컴포넌트)를
설치할 차례입니다 — [README.md의 "설치"/"설정" 섹션](README.md#설치) 참조. 설정 단계에서
**relay 서버 host:port**를 입력합니다(`18883`이 아니라 관찰 포트인 `9883`):

- **Docker(5-A)**로 띄웠다면 컨테이너를 실행한 호스트의 IP:9883
- **App(5-B)**으로 띄웠다면 기본적으로 HA 내부 도커망에서만 접근 가능합니다 — 같은 HA 안의
  커스텀 컴포넌트는 문제 없이 닿지만, 정확한 내부 호스트명이 궁금하면 HA의 app 간 통신 관련
  공식 문서를 참고하세요(app slug: `zigbang_relay`).
