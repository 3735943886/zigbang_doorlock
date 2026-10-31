# Zigbang Doorlock (Home Assistant)

> **English summary:** Home Assistant custom integration for Zigbang (Samsung SDS) smart door locks,
> sold in South Korea. The lock is re-provisioned to connect to a local relay (`zigbang-relay`) that sits
> between the lock and the Zigbang cloud; the integration reads lock state from that relay and sends remote
> unlock commands through it. Run the relay separately from Home Assistant (HA App on HAOS, or Docker /
> binary), or let the integration run it inside Home Assistant. Setup guide (Korean): [RELAY_SETUP.md](RELAY_SETUP.md).

직방(삼성SDS) 스마트 도어락을 Home Assistant에 연동하는 커스텀 컴포넌트입니다. 도어락이 직방 클라우드
대신 로컬 relay로 접속하도록 설정하고, 이 통합구성요소가 relay를 통해 상태를 받고 원격 열림을 보냅니다.
직방 앱은 계속 사용할 수 있습니다.

```
도어락 ──▶ relay ──▶ 직방 클라우드 (직방 앱)
             │
             └──▶ Home Assistant (이 통합구성요소)
```

## 설치

[RELAY_SETUP.md](RELAY_SETUP.md)의 순서대로 진행합니다.

1. 도메인 준비, TLS 인증서 발급, 접속 경로 확인
2. relay 실행 — 아래 방식 중 하나
3. 이 통합구성요소 설치·설정
4. 도어락 리셋, 재프로비저닝
5. 동작 확인

### relay 실행 방식

| 방식 | 실행 위치 | HA 재시작·업데이트·장애 시 | 통합구성요소 설정 |
|---|---|---|---|
| HA App (HAOS 권장) | HAOS/Supervised | relay 계속 동작 | 외부: App 호스트명, 9883 |
| Docker | 리눅스 머신 | relay 계속 동작 | 외부: 머신 IP, 9883 |
| 바이너리(systemd) | 리눅스 머신 | relay 계속 동작 | 외부: 머신 IP, 9883 |
| 내장 | HA 안 | relay도 함께 멈춤 | 내장: 도메인, 인증서 경로 |

HA와 별도로 실행하는 방식(App, Docker, 바이너리)을 권장합니다. 내장 방식은 HA를 재시작하면 도어락 연결이
잠시 끊기고, HA가 오래 꺼져 있으면 도어락이 절전 상태에 들어갑니다([도어락 연결 상태](#도어락-연결-상태) 참조).

## 설정

설정 → 기기 및 서비스 → 통합 구성요소 추가 → "Zigbang Doorlock"

1. 직방 아이디/비밀번호를 입력합니다(IMEI는 비워도 됨).
2. relay 방식을 선택합니다.
   - **외부**: relay 호스트와 포트(`9883`). relay에 관찰 포트 인증을 설정했다면 사용자명/비밀번호도 입력합니다.
   - **내장**: relay 도메인과 인증서/개인키 경로. 기본 경로는 HA 설정 폴더의 `zigbang_doorlock/certs/fullchain.pem`,
     `zigbang_doorlock/certs/privkey.pem`이며, HAOS에서 Let's Encrypt App을 쓰면 `/ssl/` 경로가 채워집니다.
     도어락을 18883/5683이 아닌 포트로 설정했다면 "고급"에서 포트를 맞춥니다. 모드는 기본 `shadow`(직방
     클라우드 장애 시에도 도어락에 응답), `proxy`(그대로 중계)를 선택할 수 있습니다.

통합구성요소 **옵션**에서 계정을 다시 입력하지 않고 relay 방식과 각 항목을 바꿀 수 있습니다. 같은 머신에서
방식을 바꿀 때는 두 relay가 같은 포트를 쓰므로 동시에 실행할 수 없습니다.

- 외부 → 내장: 외부 relay 중지 → 옵션에서 "내장" 저장
- 내장 → 외부: 옵션에서 "외부" 저장 → 외부 relay 시작

내장 방식이 "재시도 중"으로 남아 있으면 HA 로그에서 원인(인증서 경로, 포트 사용 중 등)을 확인하세요.

## 제공 엔티티

도어락마다 아래 엔티티가 생성됩니다.

| 종류 | 이름 | 설명 |
|---|---|---|
| lock | 도어락 | 잠금 상태, 원격 열림. 속성: 마지막 열림 방법·사용자·시각 |
| sensor | 배터리 | 배터리 % |
| sensor | 배터리 (원본값) | 배터리 원본값(진단, 기본 비활성) |
| sensor | Wi-Fi 신호 | Wi-Fi 신호세기 dBm(진단) |
| binary_sensor | relay 연결 | 도어락이 relay에 연결돼 있는지(진단). 속성: 도어락 IP:포트, 변경 시각 |
| event | 활동 기록 | 잠금/열림/키 등록/키 삭제/문 열림 경고. 속성: 열림 방법, 키 종류·이름 |
| switch | 재택 안심모드, 매직넘버 이중보안 모드, 이중 출입인증 모드, 외출 시 실내방범 모드 | 도어락 보안설정 |
| button | 클라우드 데이터 새로고침 | 키 목록과 도어락 상태를 직방 클라우드에서 다시 받아옴. 앱에서 키 이름을 바꾼 경우 등에 사용 |
| button | HA 키 초기화 (위험) | HA용 키를 모두 지우고 새로 등록(기본 비활성). 중복 키 정리용. 실행 중 실패하면 재등록 전까지 원격 열림 불가 |

원격 열림에는 이 통합구성요소가 도어락에 등록하는 전용 키(`ZBDL-HA-KEY`)를 씁니다. 직방 앱의 키 목록에
이 이름으로 표시되며, 지우면 HA가 다음 시작 시 다시 등록합니다.

### 도어락 연결 상태

- "relay 연결" 센서는 도어락이 relay에 연결되거나 끊길 때 바뀝니다. relay 0.1.8 이상이 필요하며, 그보다
  오래된 외부 relay에서는 도어락 연결 끊김이 표시되지 않습니다.
- 도어락이 30초 넘게 끊겨 있으면 "relay 연결" 센서를 제외한 엔티티가 사용 불가(unavailable)로 바뀝니다.
- 도어락이 relay에 오래 연결하지 못하면 절전 상태에 들어가며, relay가 다시 실행돼도 스스로 재접속하지
  않습니다. 문을 열었다 닫거나 키패드를 누르면 재접속합니다.

## helper/

`helper/manage_pins.py`로 도어락에 등록된 키를 조회·삭제할 수 있습니다([helper/README.md](helper/README.md)).
삭제는 relay의 관찰 포트(9883)에 접속할 수 있어야 하므로 Docker/바이너리 relay에서만 사용할 수 있습니다.
App이나 내장 방식에서 중복 HA 키를 정리하려면 "HA 키 초기화" 버튼을 쓰세요.
