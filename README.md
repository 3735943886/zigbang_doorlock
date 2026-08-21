# Zigbang Doorlock (Home Assistant)

직방(삼성SDS IHP) 스마트 도어락을 Home Assistant에 연동하는 커스텀 컴포넌트.
클라우드 폴링이 아니라 `zigbang` 로컬 relay(observer 포트)를 확인해서 상태를 갱신하고,
원격 열림도 클라우드 REST가 아니라 relay를 통해 로컬로 주입합니다.

## 사전준비

`zigbang-relay` 데몬(락↔클라우드 로컬 중계)이 먼저 떠있어야 합니다. **도메인/인증서 준비 →
relay 띄우기(Docker 또는 HA App) → 도어락 리셋 → 재프로비저닝**까지 전 과정을 이 저장소만
보고 따라할 수 있도록 [RELAY_SETUP.md](RELAY_SETUP.md)에 처음부터 끝까지 정리해뒀습니다 —
relay는 **Docker 컨테이너**(HA와 같은 머신일 필요 없음, host:port만 아래 설정에 입력) 또는
**HA App(Add-on)**, 편한 쪽으로 띄우면 됩니다. Docker 이미지는
[Docker Hub](https://hub.docker.com/r/3735943886/zigbang-relay)에 공개돼있고, 컨테이너에
필요한 설정 파일은 [`docker/`](docker/)에 들어있습니다. 물론 바이너리를 직접 systemd등으로 띄워도
됩니다.

`provision`/`relay` 바이너리와 app 저장소는 [zigbang_relay](https://github.com/3735943886/zigbang_relay)

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
   가져오는 데 씁니다. 이후 상시 폴링은 안 하지만, **HA(재)시작마다 1회** pin 레지스트리(카드/지문
   등 등록정보) 갱신을 위해 다시 로그인합니다.
2. **relay 서버**: observer 포트의 host:port. 나중에 relay가 이사가면 통합구성요소 옵션에서
   계정 재인증 없이 host/port만 수정 가능합니다.

## 제공 엔티티

값의 의미가 명확한 것만 엔티티로 노출하고, 나머지는 뺐습니다(설정값 성격이거나 로컬 제어수단이
없는 필드는 표시만 해봐야 혼란만 더함).

| 엔티티 | 설명 |
|---|---|
| `lock.<name>` | 잠금상태 + 원격열림. `last_access`/`last_method`/`last_user_name`/`last_event_at` 속성 포함 |
| `sensor.<name>_battery` | 배터리 %(보정값) |
| `sensor.<name>_battery_raw` | 배터리 원본값(진단) |
| `sensor.<name>_rssi` | Wi-Fi 신호세기(dBm, 진단) |
| `event.<name>_activity` | 잠금/열림/키등록/키삭제 이벤트. `access`/`pin_id`/`pin_type`/`pin_name` 속성 포함 |
| `button.<name>_refresh_pin_registry` | pin 레지스트리 즉시 새로고침(진단). 앱에서 pin 이름만 바꾸는 등 relay로 안 보이는 변경 반영용 |

### 원격 열림 방식 — 영구 HA 키 1개

락을 열 때마다 임시키를 등록→삭제하는 대신, **이 통합구성요소 전용 NFC 키를 최초 1회만 등록**해두고
(pin 토큰은 config 시 생성되어 `entry.data`에 영구저장) 이후 모든 unlock은 그 키로 트리거(func 407)만
보냅니다. HA가 시작될 때마다 REST로 이 영구키가 아직 등록돼있는지 확인하고, 없으면(최초 실행, 혹은 앱에서 수동
삭제된 경우) 자동으로 다시 등록합니다.

## helper/

HA와 무관하게 독립 실행되는 정리용 CLI. `helper/manage_pins.py`로 등록된 pin 조회/삭제 가능
(표준 라이브러리 + mosquitto_pub/mosquitto_sub만 필요) — 자세한 건 `helper/README.md` 참조.
