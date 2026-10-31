# relay 설정 가이드

도어락이 직방 클라우드 대신 로컬 relay로 접속하도록 설정하고, 이 통합구성요소를 연결하는 전체 절차입니다.
아래 순서대로 진행하세요.

1. [도메인 준비](#1-도메인-준비)
2. [TLS 인증서 발급](#2-tls-인증서-발급)
3. [도메인 접속 경로 확인](#3-도메인-접속-경로-확인)
4. [relay 실행](#4-relay-실행)
5. [통합구성요소 설치·설정](#5-통합구성요소-설치설정)
6. [도어락 리셋](#6-도어락-리셋)
7. [도어락 재프로비저닝](#7-도어락-재프로비저닝)
8. [동작 확인](#8-동작-확인)
9. [운영](#9-운영)

relay/provision 바이너리와 Docker 이미지는 [zigbang_relay](https://github.com/3735943886/zigbang_relay)
([Releases](https://github.com/3735943886/zigbang_relay/releases), [Docker Hub](https://hub.docker.com/r/3735943886/zigbang-relay))에서
받습니다. Docker/바이너리용 설정 파일은 이 저장소의 [`docker/`](docker/)에 있습니다.

## 1. 도메인 준비

relay에 쓸 도메인을 하나 준비합니다. 무료 DDNS도 됩니다.

- [DuckDNS](https://www.duckdns.org/)
- [afraid.org (FreeDNS)](https://freedns.afraid.org/)
- 보유한 도메인의 서브도메인(예: `relay.example.com`)

A레코드에 relay를 실행할 머신의 IP를 지정합니다(사설 IP도 가능). Cloudflare 등 프록시형 DNS를 쓴다면
"DNS only"로 설정하세요.

## 2. TLS 인증서 발급

도어락은 공인 CA가 발급하고 호스트명이 일치하는 인증서만 받습니다. 1단계 도메인으로 Let's Encrypt
인증서를 발급합니다.

**HAOS**: 커뮤니티 "Let's Encrypt" App을 설치해 발급합니다. 인증서가 `/ssl/fullchain.pem`,
`/ssl/privkey.pem`에 저장되고 App이 갱신까지 처리합니다.

**그 외**: certbot으로 발급합니다.
```bash
sudo apt install certbot
sudo certbot certonly --standalone -d relay.example.com
```
인증서는 `/etc/letsencrypt/live/relay.example.com/fullchain.pem`, `privkey.pem`에 생성됩니다. 4단계에서
relay가 읽는 위치로 복사하고, 자동 갱신 시 복사되도록 설정합니다.

> 이미 Nginx Proxy Manager, Traefik, Caddy 등으로 도메인과 인증서를 관리하고 있다면 그 도메인을 써도
> 됩니다. 이 경우 프록시에서 18883/TCP, 5683/UDP를 TLS 종료 없이 그대로(패스스루) relay로 전달하고,
> 프록시가 관리하는 인증서 파일을 relay에 복사해 넣습니다.

## 3. 도메인 접속 경로 확인

도어락과 같은 내부망의 다른 기기에서 도메인으로 relay 머신에 접속되는지 확인합니다. relay를 아직
실행하지 않았다면 4단계 후에 확인해도 됩니다.

```bash
openssl s_client -connect relay.example.com:18883
```

공인 IP로 지정했는데 내부망에서 접속되지 않으면(공유기의 NAT 루프백 미지원), A레코드를 relay 머신의
사설 IP로 지정하거나 공유기/내부 DNS에서 해당 도메인만 사설 IP로 응답하게 설정합니다.

## 4. relay 실행

아래 중 하나를 선택합니다. HA와 별도로 실행하는 4-A~4-C를 권장합니다(HAOS는 4-A).

| 방법 | 실행 위치 | HA 재시작·업데이트·장애 시 |
|---|---|---|
| 4-A. HA App | HAOS/Supervised | relay 계속 동작 |
| 4-B. Docker | 리눅스 머신 | relay 계속 동작 |
| 4-C. 바이너리(systemd) | 리눅스 머신 | relay 계속 동작 |
| 4-D. 내장 | HA 안 | relay도 함께 멈춤 |

모든 방법에서 도어락이 접속하는 포트는 18883/TCP, 5683/UDP이고, 통합구성요소는 관찰 포트 9883으로
relay에 연결합니다. 9883은 로컬신뢰망 밖에서 접근할 수 없게 하세요.

### 4-A. HA App

1. HA → 설정 → 애드온(앱) → 스토어 → 우측 상단 ⋮ → 저장소 → `https://github.com/3735943886/zigbang_relay`
   추가 → "Zigbang Doorlock Relay" 설치.
2. App 데이터 폴더(`/data`, Samba 공유나 File editor App으로 접근)에 이 저장소의 `docker/rules/` 폴더를
   `rules/`로 복사합니다.
3. 구성 탭에서 설정하고 저장합니다.
   - `cert_name`: 1단계 도메인
   - `cert_file`/`cert_key`: Let's Encrypt App을 쓰면 기본값(`/ssl/...`) 그대로
   - `route`의 `mode`: `shadow` 권장(직방 클라우드 장애 시에도 도어락에 응답), 그대로 중계만 하려면 `proxy`
4. App을 시작하고 로그 탭에 `relay 시작 — routes=2`가 표시되는지 확인합니다.
5. App 정보 페이지의 호스트명을 기록합니다(5단계에서 입력, 예: `e6fe778a-zigbang-relay`).

### 4-B. Docker

```bash
mkdir -p /opt/zigbang-relay/certs
cp -r docker/rules /opt/zigbang-relay/rules
cp docker/relay.toml.example /opt/zigbang-relay/relay.toml
sudo install -m 0644 /etc/letsencrypt/live/relay.example.com/fullchain.pem /opt/zigbang-relay/certs/
sudo install -m 0640 /etc/letsencrypt/live/relay.example.com/privkey.pem   /opt/zigbang-relay/certs/
```

`/opt/zigbang-relay/relay.toml`을 수정합니다.
- `cert_name`: 1단계 도메인
- 각 `[[route]]`의 `mode`: `shadow` 권장(기본값 `proxy`)

```bash
docker run -d --restart=always --name zigbang-relay \
  --user $(id -u):$(id -g) \
  -p 18883:18883 -p 5683:5683/udp -p 9883:9883 \
  -v /opt/zigbang-relay:/data \
  3735943886/zigbang-relay:stable
docker logs -f zigbang-relay   # relay 시작 — routes=2 확인
```
`--user`에 지정한 유저가 `/opt/zigbang-relay`에 쓰기 권한이 있고 `certs/privkey.pem`을 읽을 수 있어야 합니다.

인증서 자동 갱신 — `/etc/letsencrypt/renewal-hooks/deploy/zigbang-relay.sh`를 만들고 `chmod +x`:
```bash
#!/usr/bin/env bash
set -e
D=relay.example.com
if [[ "$RENEWED_LINEAGE" == *"/$D" ]]; then
  install -m 0644 "$RENEWED_LINEAGE/fullchain.pem" /opt/zigbang-relay/certs/fullchain.pem
  install -m 0640 "$RENEWED_LINEAGE/privkey.pem"   /opt/zigbang-relay/certs/privkey.pem
fi
```
인증서가 바뀌면 relay가 재시작 없이 새 인증서를 사용합니다.

### 4-C. 바이너리(systemd)

4-B와 같은 폴더 구성(`/opt/zigbang-relay/`의 `relay.toml`, `rules/`, `certs/`)을 준비하고 `relay.toml`을
같은 방법으로 수정한 뒤, 아키텍처에 맞는 relay 바이너리를 받습니다(`relay-linux-amd64`/`arm64`/`armv7`/`armv6`).

```bash
curl -L -o /opt/zigbang-relay/relay \
  https://github.com/3735943886/zigbang_relay/releases/latest/download/relay-linux-arm64
chmod +x /opt/zigbang-relay/relay
```

`/etc/systemd/system/zigbang-relay.service`:
```ini
[Unit]
Description=Zigbang doorlock relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# /opt/zigbang-relay 소유 유저
User=pi
WorkingDirectory=/opt/zigbang-relay
ExecStart=/opt/zigbang-relay/relay relay.toml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zigbang-relay
journalctl -u zigbang-relay -f   # relay 시작 — routes=2 확인
```
인증서 자동 갱신은 4-B의 deploy hook을 그대로 사용합니다.

### 4-D. 내장

별도로 실행할 것은 없습니다. 인증서만 준비하고 5단계에서 "내장"을 선택합니다.

- 인증서를 HA 설정 폴더의 `zigbang_doorlock/certs/fullchain.pem`, `zigbang_doorlock/certs/privkey.pem`에
  둡니다(HAOS에서 Let's Encrypt App을 쓰면 생략, `/ssl/` 경로가 자동으로 채워짐). 갱신 시 이 위치로
  복사되도록 4-B의 deploy hook에서 복사 경로만 바꿔 사용합니다.
- HA가 18883/TCP, 5683/UDP를 직접 엽니다. 같은 머신에서 다른 relay가 이 포트를 쓰고 있으면 먼저 중지하세요.
  HA를 Docker로 실행 중이면 `--network=host`여야 합니다.
- 지원 아키텍처: Linux x86_64/aarch64.

## 5. 통합구성요소 설치·설정

1. HACS → Integrations → 우측 상단 메뉴 → Custom repositories에 이 저장소 URL을 "Integration"으로 추가하고
   "Zigbang Doorlock"을 설치한 뒤 HA를 재시작합니다. (수동 설치: `custom_components/zigbang_doorlock`을 HA의
   `config/custom_components/`에 복사 후 재시작)
2. 설정 → 기기 및 서비스 → 통합 구성요소 추가 → "Zigbang Doorlock".
3. 직방 아이디/비밀번호를 입력합니다(IMEI는 비워도 됨).
4. relay 방식을 선택하고 입력합니다.
   - **외부** (4-A~4-C)
     - 호스트: 4-A는 App 호스트명, 4-B/4-C는 relay 머신의 IP
     - 포트: `9883`
     - relay 사용자명/비밀번호: relay에 관찰 포트 인증을 설정한 경우만
   - **내장** (4-D)
     - relay 도메인: 1단계 도메인
     - 인증서/개인키 경로: 4-D에서 둔 경로(기본값 그대로면 수정 불필요)
     - 고급(접힘): 7단계에서 18883/5683이 아닌 포트를 쓸 경우 포트 변경, 모드는 기본 `shadow`

내장을 선택했는데 설정 후 통합구성요소가 "재시도 중"이면 HA 로그에서 원인(인증서 경로, 포트 사용 중 등)을
확인하세요.

## 6. 도어락 리셋

relay가 실행 중인 상태에서 진행합니다. 리셋 직후 7단계를 이어서 진행해야 합니다.

(SHP-DP960SG 기준) 문을 연 상태에서 `reg` 버튼을 5초간 누릅니다. 비밀번호 입력을 요구하면 등록된
비밀번호와 `#`을 누릅니다. 도어락이 재연동 대기 상태(자체 WiFi 송출)가 됩니다.

다른 모델은 제조사 설명서의 리셋 절차를 따르세요. 이 리셋은 연동 정보만 초기화하며, 등록된 카드·지문·
비밀번호는 유지됩니다.

## 7. 도어락 재프로비저닝

### 7-1. 준비물

WiFi를 쓸 수 있는 기기 하나(노트북, 또는 아이폰 + [iSH](https://ish.app/)).

### 7-2. provision 받기

[Releases](https://github.com/3735943886/zigbang_relay/releases)에서 기기에 맞는 파일을 받습니다.

| 환경 | 파일 |
|---|---|
| Linux x86-64 | `provision-linux-amd64` |
| Linux ARM64 | `provision-linux-arm64` |
| Linux ARM 32비트 | `provision-linux-armv7` / `provision-linux-armv6` |
| iPhone(iSH) / 32비트 x86 리눅스 | `provision-linux-i686` |
| macOS (Intel / Apple Silicon) | `provision-macos-amd64` / `provision-macos-arm64` |
| Windows x86-64 | `provision-windows-amd64.zip`(압축 해제 후 `provision.exe`) |

Linux/macOS/iSH: `chmod +x provision-linux-amd64`

### 7-3. 실행

1. 기기를 인터넷이 되는 WiFi에 연결한 상태에서 실행합니다(아래 예시의 값을 실제 값으로).
   ```bash
   ./provision-linux-amd64 \
     --login-id 직방계정ID --login-pass 비번 \
     --service-url relay.example.com --service-port 18883 \
     --service-url2 relay.example.com --service-port2 5683 \
     --home-ssid 우리집WiFi --home-pass WiFi비번
   ```
2. 화면에 `도어락 SoftAP 대기 중`이 표시되면, 기기의 WiFi를 도어락이 송출하는 WiFi(보통 모델명이
   포함된 이름)로 바꿉니다. 연결되면 자동으로 진행됩니다.
3. `커미셔닝 완료`가 표시되면 기기를 원래 WiFi로 되돌립니다.

- `--service-url`/`--service-url2`에는 1단계 도메인을 정확히 입력합니다. 도어락은 이후 이 주소로만 접속합니다.
- 포트를 18883/5683이 아닌 값으로 쓰면 relay 설정(내장은 고급 섹션)도 같은 값으로 맞춥니다.
- 다시 실행할 때마다 1~3을 반복합니다(인터넷 WiFi에서 시작 → 도어락 WiFi로 전환).
- 먼저 도어락 상태를 바꾸지 않고 연결만 확인하려면 `--handshake-only`를 붙여 실행합니다.

자주 쓰는 옵션(전체: `--help`):

| 옵션 | 설명 |
|---|---|
| `--service-url`, `--service-port` | 도어락에 넣을 relay 주소(TCP). 1단계 도메인 + `18883` |
| `--service-url2`, `--service-port2` | 도어락에 넣을 relay 주소(UDP). 1단계 도메인 + `5683` |
| `--login-id`, `--login-pass` | 직방 계정 |
| `--home-ssid`, `--home-pass` | 도어락이 접속할 집 WiFi |
| `--auth-mode` | 집 WiFi 인증방식. 기본 `1`(WPA) 유지 |
| `--name` | 도어락 표시 이름(기본 `ZigbangLock`) |
| `--handshake-only` | 도어락 상태를 바꾸지 않고 연결만 확인 |
| `--legacy` | 구형 모델에서 기본 설정으로 실패할 때 시도 |

"공장초기화(페어링모드) 후 재시도" 오류가 나면 6단계 리셋이 되지 않은 상태입니다. 실패하면 6단계부터
다시 진행합니다. 실행 폴더에 남는 `.log`/`.capture` 파일은 문의 시 첨부하세요.

## 8. 동작 확인

- HA의 도어락 기기에서 "relay 연결" 센서가 켜져 있으면 성공입니다.
- relay 로그에서도 도어락 접속(`도어락 TLS OK`)을 확인할 수 있습니다(4-A 로그 탭, 4-B `docker logs`,
  4-C `journalctl`, 4-D HA 로그).
- 접속이 없으면 집 WiFi 정보(`--auth-mode` 포함)와 3단계 접속 경로를 확인합니다.

## 9. 운영

- **도어락 절전**: 도어락이 relay에 오래 연결하지 못하면 절전 상태에 들어가며, relay가 다시 실행돼도
  스스로 재접속하지 않습니다. 문을 열었다 닫거나 키패드를 누르면 재접속합니다.
- **내장 방식**: relay가 HA와 함께 시작·종료됩니다. HA를 재시작하면 도어락 연결이 잠시 끊기고, HA가 오래
  꺼져 있으면 위의 절전 상태가 됩니다.
- **인증서 갱신**: HAOS Let's Encrypt App(4-A/4-D)은 자동입니다. 그 외에는 deploy hook으로 relay가 읽는
  위치에 복사되는지 확인하세요.
- **relay 방식 변경**(같은 머신에서): 두 relay는 같은 포트를 쓰므로 동시에 실행할 수 없습니다.
  - 외부 → 내장: 외부 relay 중지 → 통합구성요소 옵션에서 "내장" 저장
  - 내장 → 외부: 통합구성요소 옵션에서 "외부" 저장 → 외부 relay 시작
