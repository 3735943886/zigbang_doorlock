# Zigbang Doorlock (Home Assistant)

> **English summary:** Home Assistant custom integration for Zigbang (Samsung SDS) smart door locks,
> sold in South Korea. Instead of polling the cloud, it reads lock state from a local relay
> (`zigbang-relay`) that sits between the lock and the Zigbang cloud, and sends remote unlock commands
> through that relay. By default the integration runs the relay itself inside Home Assistant
> (installed from the [`zigbang-relay`](https://pypi.org/project/zigbang-relay/) PyPI package, Linux
> x86_64/aarch64); you can instead connect to a relay you run yourself as a Docker container or plain
> binary. The old HA App (add-on) is deprecated. Setup guide (Korean): [RELAY_SETUP.md](RELAY_SETUP.md).

직방(삼성SDS) 스마트 도어락을 Home Assistant에 연동하는 커스텀 컴포넌트입니다. 클라우드를 폴링하지
않고, 도어락과 직방 클라우드 사이에 둔 로컬 relay(`zigbang-relay`)의 트래픽을 보고 상태를 갱신하며,
원격 열림도 relay를 통해 로컬로 보냅니다.

```
도어락 ──MQTTS 18883 / UDP 5683──▶ relay ──▶ 직방 클라우드 (앱은 그대로 동작)
                                     │
                                     └── observer(관찰 포트) ◀── 이 통합구성요소
```

## relay 실행 방식

| | 내장 (권장) | 외부 |
|---|---|---|
| 어떻게 | 이 통합구성요소가 relay를 HA 안에서 직접 실행 | Docker 컨테이너 또는 바이너리를 직접 실행(systemd 등)하고 HA는 observer 포트로 연결 |
| 준비물 | 도메인 + 인증서만 | relay 서버, 설정 파일([`docker/`](docker/)), 방화벽 |
| relay 설치 | PyPI 패키지 [`zigbang-relay`](https://pypi.org/project/zigbang-relay/)가 내장 모드를 고를 때 자동 설치(Linux x86_64/aarch64) | [Docker Hub](https://hub.docker.com/r/3735943886/zigbang-relay) 이미지 또는 [zigbang_relay Releases](https://github.com/3735943886/zigbang_relay/releases) 바이너리 |
| observer 노출 | `127.0.0.1`의 임의 포트에만 열림 — 외부 노출 없음 | 기본 9883, 로컬신뢰망 밖으로 막아야 함 |
| HA 재시작 시 | relay도 같이 재시작. HA가 오래 꺼져있었다면 도어락이 절전 상태라 더 걸릴 수 있음 — 문을 열었다 닫는 등 조작하면 바로 재접속([아래](#도어락-연결-상태와-unavailable) 참조) | relay는 계속 떠있어 도어락 영향 없음 |

HA App(Add-on)은 **지원 중단**됐습니다. 기존 App 사용자는 내장 모드로 옮기세요 — 도메인·인증서·포트가
그대로라 도어락 재프로비저닝은 필요 없습니다([RELAY_SETUP.md 5-C](RELAY_SETUP.md#5-c-ha-appadd-on--지원-중단)).

## 사전준비

도어락이 직방 클라우드 대신 우리 relay로 접속하도록 만들어야 합니다: **도메인/인증서 준비 → relay
띄우기 → 도어락 리셋 → 재프로비저닝**. 전 과정을 [RELAY_SETUP.md](RELAY_SETUP.md)에 처음부터 끝까지
정리해뒀습니다. 내장 모드는 relay가 HA와 함께 뜨므로, 이 통합구성요소를 먼저 설치/설정한 뒤 도어락을
리셋·재프로비저닝합니다.

`provision`/`relay` 바이너리는 [zigbang_relay](https://github.com/3735943886/zigbang_relay)에서 배포합니다.

## 설치

### HACS
1. HACS → Integrations → 우측 상단 메뉴 → Custom repositories
2. 이 저장소 URL 추가, 카테고리 "Integration"
3. "Zigbang Doorlock" 검색 후 설치, HA 재시작

### 수동
`custom_components/zigbang_doorlock`를 HA의 `config/custom_components/`에 복사 후 재시작.

## 설정

설정 → 기기 및 서비스 → 통합 구성요소 추가 → "Zigbang Doorlock"

1. **직방 계정**: 아이디/비밀번호/IMEI(선택, 비우면 자동생성 후 고정 저장). 등록된 도어락 목록을
   가져오는 데 씁니다. 상시 폴링은 안 하지만, **HA(재)시작마다 1회** pin 레지스트리(카드/지문 등
   등록정보)와 보안설정 상태를 받아오려고 다시 로그인합니다.
2. **relay 방식** 메뉴에서 선택:
   - **내장 (권장)**
     - relay 도메인: 프로비저닝 때 도어락에 주입한 도메인(예: `relay.example.duckdns.org`)
     - 인증서/개인키 경로: 기본 `<config>/zigbang_doorlock/certs/fullchain.pem`, `privkey.pem`.
       HAOS에서 Let's Encrypt App이 `/ssl/fullchain.pem`, `/ssl/privkey.pem`을 이미 만들어뒀으면 그
       경로가 기본값으로 채워집니다. 파일 이름은 상관없고 PEM 형식이면 됩니다.
     - 접힌 **고급** 섹션
       - MQTTS 포트(기본 18883) / UDP 포트(기본 5683): 프로비저닝 때 쓴 `--service-port`/`--service-port2`와 같아야 함
       - 모드: `shadow`(기본 — 직방 클라우드가 끊겨도 relay가 도어락에 대신 응답) / `proxy`(그대로 중계만)
     - HA가 도어락용 포트를 직접 엽니다. 같은 호스트에서 다른 relay가 같은 포트를 쓰고 있으면 기동에
       실패하니 먼저 내리세요. HA Container로 설치했다면 `--network=host`여야 도어락이 들어올 수 있습니다.
   - **외부**: relay observer 포트의 host:port(기본 9883) + 선택적 relay 사용자명/비밀번호.

나중에 통합구성요소 **옵션**에서 계정 재인증 없이 relay 방식 전환, 인증서 경로, 포트, 모드, 외부 relay
주소를 바꿀 수 있습니다.

내장 모드 기동에 실패하면(인증서 없음, 포트 사용중 등) 통합구성요소가 "재시도 중" 상태로 남고 이유가
HA 로그에 찍힙니다.

### 데이터 폴더

내장 모드는 HA 설정 폴더의 `zigbang_doorlock/`을 씁니다.

| 경로 | 내용 |
|---|---|
| `zigbang_doorlock/certs/` | 인증서 기본 위치(직접 넣음 — 갱신 때마다 여기로 복사되게 해두세요) |
| `zigbang_doorlock/state/replay.json` | shadow 모드 재생 캐시(클라우드 장애 때 대신 응답하는 데 씀, 자동 생성) |
| `zigbang_doorlock/logs/` | relay 로그(기동마다 새 파일, 최근 5개만 보관) |

## 제공 엔티티

값의 의미가 명확한 것만 엔티티로 노출했습니다(설정값 성격이거나 로컬 제어수단이 없는 필드는 표시만
해봐야 혼란만 더함). 도어락마다 아래 엔티티가 생깁니다.

| 종류 | 이름 | 설명 |
|---|---|---|
| lock | 도어락 | 잠금상태 + 원격열림. `last_access`/`last_method`/`last_user_name`/`last_unlock_at` 속성(전체 기록은 활동 기록 이벤트) |
| sensor | 배터리 | 배터리 %(보정값) |
| sensor | 배터리 (원본값) | 배터리 원본값(진단, 기본 비활성) |
| sensor | Wi-Fi 신호 | Wi-Fi 신호세기 dBm(진단) |
| binary_sensor | relay 연결 | 도어락이 지금 relay에 붙어있는지(진단). `peer`(도어락 IP:포트), `changed_at` 속성 |
| event | 활동 기록 | 잠금/열림/키 등록/키 삭제/문 열림 경고. `access`/`pin_id`/`pin_type`/`pin_name` 속성 |
| switch | 재택 안심모드, 매직넘버 이중보안 모드, 이중 출입인증 모드, 외출 시 실내방범 모드 | 도어락 보안설정(설정) |
| button | 클라우드 데이터 새로고침 | pin 레지스트리와 도어락 상태를 클라우드에서 다시 받아옴(설정). 앱에서 pin 이름만 바꾸는 등 relay로 안 보이는 변경 반영용 |
| button | HA 키 초기화 (위험) | HA 전용 키를 전부 지우고 새로 하나 등록(설정, 기본 비활성). 중복 키 정리용 최후 수단 — 실행 중 실패하면 그 사이 원격 열림이 안 됨 |

### 도어락 연결 상태와 unavailable

- **relay 연결** 센서는 도어락이 relay에 붙고 끊기는 순간 바로 바뀝니다. relay 0.1.8+가 보내는 세션
  이벤트를 쓰므로 내장 모드는 항상 정확합니다. 외부 relay가 그보다 오래된 버전이면 도어락 메시지가 보일
  때 "연결"로만 바뀌고, "끊김"은 observer 연결이 끊길 때만 표시됩니다.
- 도어락이 **30초 넘게** 끊겨있으면 relay 연결 센서를 뺀 나머지 엔티티가 unavailable이 됩니다. relay는
  직방 클라우드 연결이 리셋될 때마다(하루 몇 번) 도어락 세션을 일부러 끊고 도어락이 2~3초 안에 다시
  붙는데, 이 정도 깜빡임은 무시합니다.
- HA가 relay(observer)에 연결되지 못하면 모든 도어락을 "끊김"으로 봅니다.
- 도어락은 relay에 일정 시간 이상 연결하지 못하면(예: 내장 모드에서 HA가 오래 꺼져있었을 때) 배터리를
  아끼려고 **절전(sleep) 상태**에 들어가고, 그 뒤엔 relay가 다시 떠도 재접속까지 오래 걸립니다. 바로
  깨우려면 **문을 열었다 닫거나 키패드를 누르는 등 도어락을 직접 조작**하세요 — 도어락이 깨어나면서
  곧바로 relay에 다시 접속합니다.

### 원격 열림 방식 — 영구 HA 키 1개

락을 열 때마다 임시키를 등록→삭제하는 대신, **이 통합구성요소 전용 NFC 키(`ZBDL-HA-KEY`)를 최초 1회만
등록**해두고(pin 토큰은 설정 시 생성되어 `entry.data`에 영구저장) 이후 모든 unlock은 그 키로 트리거(func
407)만 보냅니다. HA가 시작될 때마다 REST로 이 키가 아직 등록돼있는지 확인하고, 없으면(최초 실행, 혹은
앱에서 수동 삭제된 경우) 도어락 트래픽이 처음 보일 때 자동으로 다시 등록합니다.

## helper/

HA와 무관하게 독립 실행되는 정리용 CLI입니다. `helper/manage_pins.py`로 등록된 pin을 조회/삭제할 수
있습니다(표준 라이브러리 + `mosquitto_pub`/`mosquitto_sub`만 필요) — 자세한 건
[helper/README.md](helper/README.md) 참조. 조회는 REST만 쓰지만 삭제는 relay observer에 직접 붙어야
해서, 내장 모드(observer가 HA 안의 `127.0.0.1`에만 열림)에선 삭제가 안 됩니다 — 중복 HA 키 정리는
"HA 키 초기화" 버튼을 쓰세요.
