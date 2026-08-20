# Zigbang Doorlock (Home Assistant)

직방(삼성SDS IHP) 스마트 도어락을 Home Assistant에 연동하는 커스텀 컴포넌트.
클라우드 폴링이 아니라 `zigbang` 로컬 relay(observer 포트)를 push 로 구독해서 상태를 갱신하고,
원격 열림도 클라우드 REST가 아니라 relay를 통해 로컬로 주입합니다.

## 사전준비

`zigbang` relay 데몬이 이미 떠있어야 합니다(systemd/Docker/HA add-on 중 택1, 별도 저장소). HA
add-on으로 띄운 경우 observer 포트(기본 9883)가 기본적으로 host에 안 열려있으니 같은 docker
네트워크 안에서 컨테이너명으로 접근하거나, 필요시 직접 포트를 열어주세요.

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
   등 등록정보) 갱신을 위해 다시 로그인합니다 — 아래 "열림 방식 세분화" 참조.
2. **relay 서버**: observer 포트의 host:port. 나중에 relay가 이사가면 통합구성요소 옵션에서
   계정 재인증 없이 host/port만 수정 가능합니다.

## 제공 엔티티

값의 의미가 명확한 것만 엔티티로 노출하고, 나머지는 뺐습니다(설정값 성격이거나 로컬 제어수단이
없는 필드는 표시만 해봐야 혼란만 더함).

| 엔티티 | 설명 |
|---|---|
| `lock.<name>` | 잠금상태 + 원격열림. `last_access`/`last_method`/`last_user_name`/`last_event_at` 속성 포함 |
| `sensor.<name>_battery` | 배터리 %(추정치, 아래 참조) |
| `sensor.<name>_battery_raw` | 배터리 원본값(진단, 기본 비활성) |
| `sensor.<name>_rssi` | Wi-Fi 신호세기(dBm, 진단) |
| `event.<name>_activity` | 잠금/열림/키등록/키삭제 이산 이벤트. `access`/`pin_id`/`pin_type`/`pin_name` 속성 포함 |
| `button.<name>_refresh_pin_registry` | pin 레지스트리 즉시 새로고침(진단). 앱에서 pin 이름만 바꾸는 등 relay로 절대 안 보이는 변경 반영용 |

### 원격 열림 방식 — 영구 HA 키 1개

락을 열 때마다 임시키를 등록→삭제하는 대신, **이 통합구성요소 전용 NFC 키를 최초 1회만 등록**해두고
(pin 토큰은 config 시 생성되어 `entry.data`에 영구저장) 이후 모든 unlock은 그 키로 트리거(func 407)만
보냅니다. 등록/삭제를 매번 반복하지 않으므로 pin 슬롯이 쌓이는 문제가 구조적으로 없습니다(실기
테스트 중 등록만 되고 삭제가 안 된 임시키가 실제로 남았던 걸 확인 후 이 방식으로 전환했습니다 —
`helper/manage_pins.py`가 그때 만든 정리용 도구입니다). HA가 시작될 때마다 REST로 이 영구키가 아직
등록돼있는지 확인하고, 없으면(최초 실행, 혹은 앱에서 수동 삭제된 경우) 자동으로 다시 등록합니다.

### 열림 방식(`last_method`) 세분화

`IDPEVENT`(622) 자체엔 `pinType`이 없지만 `pinId`는 있고, `pinId → pinType/pinName` 레지스트리와
대조하면 풀립니다(공식앱도 같은 방식, PROTOCOL.md §9-1/§10). 이 레지스트리는 **두 경로로** 채웁니다:

1. **REST(`v20/doorlockctrl/getdoorkeys`, 실계정으로 실측 검증됨)** — HA 시작마다 1회, 등록된
   pin 전체(카드/지문/임시키 등)를 한 번에 받습니다. **필수 경로**입니다 — relay observer는 tap이라
   HA가 뜨기 전 트래픽은 못 보는데, 실측 로그 확인 결과 `pinInfoXXX`는 세션 부트스트랩에도 항상
   오는 게 아니라 그 슬롯이 등록/터치될 때만 개별로 오고, **실제 카드 언락 순간에도 안 실려있던
   사례**가 있어서 relay 관찰만으론 사실상 영구히 못 채워질 수 있습니다.
2. **relay observer의 `Basic-AttrGroup` push** — 위 REST 이후 새로 등록/변경된 슬롯을 보조적으로
   반영(재시작 없이도 최신 유지).

REST 호출이 실패해도(계정 문제, 네트워크 등) lock/battery/rssi 등 핵심 기능은 relay로 그대로
동작하고, `last_method`/`last_user_name`만 `external`(대분류)로 폴백합니다.

`access=="RFC"`(카드/키패드/지문 등 외부 물리인증)일 때만 이 레지스트리 조회를 적용합니다:
- `SVR`(원격열림): pinId가 원격열림용 임시 NFC키(HA 자신의 injection 포함)를 가리켜서 pinType이
  항상 NFC로 나옴 — 실제 구현 detail일 뿐이라 `remote`로 고정.
- `AUTO`/`INDOOR`: pinId가 실측상 항상 `0`(MST, 필러값)이라 진짜 자격증명이 아님.

`RFC`일 때만 `last_method`가 `keytag`/`fingerprint`/`keypad` 등으로 세분화되고, 등록시 이름을
지정해뒀다면 `last_user_name`에 그 이름이 그대로 채워집니다(예: 실계정 테스트에서 카드에 등록된
실명이 그대로 노출됨 확인 — 로그북/히스토리에 남으니 원치 않으면 `event.*_activity`/`lock.*` 쪽
`pin_name`/`last_user_name` 속성을 대시보드에서 안 보이게 가리는 걸 권장).

## 알려진 제한

- **`sId` 학습 지연**: 도어락별 세션 식별자(`sId`)는 설계상 고정값이 아니라 relay 트래픽에서
  실시간으로 학습합니다. HA 재시작 직후 도어락이 아직 아무 트래픽도 안 보냈다면, 그 사이에 열림을
  시도하면 실패합니다(에러 메시지로 안내) — 락이 한 번이라도 신호를 보내면(하트비트 주기 이내) 곧 해결.
- **배터리 %는 추정치**: 도어락이 보고하는 raw 값은 선형 % 가 아니라 임계값 기반 코드에 가까워
  보입니다(`sensor.*_battery_raw`로 원본값 확인 가능). `zigbang_doorlock_pyscript`와 동일한 매핑을
  사용했습니다.

## helper/

HA와 무관하게 독립 실행되는 정리용 CLI. `helper/manage_pins.py`로 등록된 pin 조회/삭제 가능
(표준 라이브러리 + mosquitto_pub/mosquitto_sub만 필요) — 자세한 건 `helper/README.md` 참조.