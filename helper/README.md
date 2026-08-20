# helper

HA 통합구성요소와 무관하게 독립 실행되는 운영/정리용 스크립트 모음.

## manage_pins.py

등록된 pin(카드/지문/임시키 등) 조회 및 삭제. 테스트하다 쌓인 임시키 등을 나중에 직접 정리하고
싶을 때 사용. 표준 라이브러리 + `mosquitto_pub`/`mosquitto_sub`(delete 시에만)만 있으면 됨 —
`custom_components/` 안 코드나 HA 설치 없이도 실행 가능.

```bash
# 조회(REST만, relay 접근 불필요)
python3 helper/manage_pins.py --id <직방ID> list

# 삭제(REST로 대상 확인 + relay observer로 삭제 injection + REST 재확인)
python3 helper/manage_pins.py --id <직방ID> delete --pin-id 5 --relay-host <relay IP>
```

비밀번호는 인자로 주지 않으면 프롬프트로 물어봄. `--sid`를 생략하면 relay를 잠깐(기본 20초)
관찰해서 자동으로 얻으려고 시도하는데, 하트비트 주기가 길어(~25분) 못 잡을 수 있음 — 그럴 땐
이전에 관측된 값을 `--sid`로 직접 넘기면 됨(같은 계정/락이면 보통 고정값).
