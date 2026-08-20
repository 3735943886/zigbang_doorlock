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
   가져오는 데 **1회만** 쓰입니다 — 이후 재로그인하지 않습니다.
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

### 열림 방식(`last_method`) 세분화

`IDPEVENT`(622) 자체엔 `pinType`이 없지만, `pinId`는 있고 `Basic-AttrGroup`이 흘려주는
`pinInfoXXX` 레지스트리(pinId→pinType/pinName)와 대조하면 그 자리에서 풀립니다 — 이것도 relay
observer(9883) 평문 JSON 안에 다 있는 정보라 별도 TLS 분석이 필요 없습니다(공식앱도 같은 방식으로
"지문"/"카드" 등을 표시함, PROTOCOL.md §9-1/§10 및 실측 fixture로 검증).

다만 `access=="RFC"`(카드/키패드/지문 등 외부 물리인증)인 경우만 이 레지스트리 조회를 적용합니다:
- `SVR`(원격열림): pinId가 원격열림용 임시 NFC키(HA 자신의 injection 포함)를 가리켜서 pinType이
  항상 NFC로 나옴 — 실제 구현 detail일 뿐이라 `remote`로 고정.
- `AUTO`/`INDOOR`: pinId가 실측상 항상 `0`(MST, 필러값)이라 진짜 자격증명이 아님.

`RFC`일 때만 `last_method`가 `keytag`/`fingerprint`/`keypad` 등으로 세분화되고, 등록시 이름을
지정해뒀다면 `last_user_name`도 채워집니다. 레지스트리가 아직 동기화 전(HA 갓 재시작 등)이거나
낯선 pinId면 `external`로 폴백합니다.

## 알려진 제한

- **`sId` 학습 지연**: 도어락별 세션 식별자(`sId`)는 설계상 고정값이 아니라 relay 트래픽에서
  실시간으로 학습합니다. HA 재시작 직후 도어락이 아직 아무 트래픽도 안 보냈다면, 그 사이에 열림을
  시도하면 실패합니다(에러 메시지로 안내) — 락이 한 번이라도 신호를 보내면(하트비트 주기 이내) 곧 해결.
- **배터리 %는 추정치**: 도어락이 보고하는 raw 값은 선형 % 가 아니라 임계값 기반 코드에 가까워
  보입니다(`sensor.*_battery_raw`로 원본값 확인 가능). `zigbang_doorlock_pyscript`와 동일한 매핑을
  사용했습니다.