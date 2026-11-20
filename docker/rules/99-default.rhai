// 직방(삼성SDS ocp) 도어락 규칙(default/fallback). Rust 엔 프로토콜 문자열·필드명 0줄 — 전부 여기
// (private/DESIGN.md, 훅 목록은 src/rules.rs 상단). 파일명 99- 라서 다른 규칙파일들보다 항상 나중에
// 검사됨(우선순위 최저). 다른 기기를 지원하려면 이 파일을 고치지 말고 앞번호 파일을 추가할 것.

// ═══════════ 프로토콜 훅(모든 모드, proxy 포함) ═══════════

// clientId = "{sId}+{tpId}" (예 "CB00000000+SHP-DP960SG.AABBCCDDEEFF"). 기기 id = tpId.
// authToken 은 Local(cloudless) 부트스트랩 때 우리가 발급하는 값(on_session_start 참조).
fn identify(client_id) {
    let i = client_id.index_of('+');
    let sid = if i < 0 { "" } else { client_id.sub_string(0, i) };
    let tpid = if i < 0 { client_id } else { client_id.sub_string(i + 1) };
    #{ device: tpid, sId: sid, tpId: tpid, tId: tpid, authToken: uuid() }
}

// 요청(Q)↔응답(A) 은 msgId echo 로 짝지어짐 → 로컬 선응답 후 서버의 늦은 중복 ack drop 키.
fn correlation_id(msg) {
    let id = msg.msgId;
    if type_of(id) == "string" && id != "" { id } else { () }
}

// 세션 ctx 학습:
// - cloud2dev: 클라우드가 발급/사용하는 진짜 authToken(로컬주입 미러링에 씀, on_local_inject).
//   + 이름 있는 NFC e-key 등록(408)의 이름→pin 토큰(`nfcTok_<이름>`) — 슬롯삭제(410) 때 replay 키를 찾는 용도.
// - dev2cloud: 락이 올리는 키 슬롯표(Basic-AttrGroup 의 pinInfoNNN)로 `pinSlot_<N>` → "<pinType>/<pinName>".
//   빈 객체(pinInfo007:{}) = 슬롯 비워짐 → "".
//   ※ 락은 슬롯 변경 시 그 슬롯만, 키싱크 때 전체를 올림 — 그걸 못 본 세션에선 예전 키의 슬롯을 모름
//   (그 경우 410 이 와도 replay 는 못 지움. e-key 삭제는 실측상 420 이라 영향 적음).
fn learn(msg, topic, dir) {
    let out = #{};
    if dir == "cloud2dev" {
        let tok = msg.authToken;
        if type_of(tok) == "string" && tok != "" {
            out.cloudAuthToken = tok;
        }
        let a = nfc_ekey(msg);
        if a != () {
            out[`nfcTok_${a.pinName}`] = a.pin;
        }
    }
    if dir == "dev2cloud" && msg.msgCode == "Basic-AttrGroup" && type_of(msg.data) == "map" {
        for k in msg.data.keys() {
            if k.starts_with("pinInfo") {
                let p = msg.data[k];
                let v = "";
                if type_of(p) == "map" && type_of(p.pinType) == "string" && type_of(p.pinName) == "string" {
                    v = `${p.pinType}/${p.pinName}`;
                }
                out[`pinSlot_${parse_int(k.sub_string(7))}`] = v;
            }
        }
    }
    if out.len() > 0 { out } else { () }
}

// 클라우드→락 "이름 있는 NFC e-key 등록"(MSGBA0300001 func 408, pinType NFC) 이면 그 arg, 아니면 ().
// 폰 e-key(멤버별, pinSort PMT)가 이것 — 원격열림 임시키는 같은 408 이지만 이름이 없음.
fn nfc_ekey(msg) {
    if msg.msgCode != "MSGBA0300001" || type_of(msg.data) != "map" || msg.data.func != 408 { return (); }
    let a = msg.data.arg;
    if type_of(a) != "map" || a.pinType != "NFC" { return (); }
    if type_of(a.pinName) != "string" || a.pinName == "" || type_of(a.pin) != "string" || a.pin == "" { return (); }
    a
}

// 관찰자(HA 등)가 나중에 붙어도 마지막 상태를 바로 받게 dataBus 를 기기별로 보관.
// (세션별 ocp/{sId}/{tpId} 는 tId/msgId 가 그 순간에 묶여있어 재생하면 죽은 스냅샷이라 제외.)
fn retain_key(topic, msg) {
    if topic == "ocp/dataBus" {
        let tp = msg.tpId;
        if type_of(tp) == "string" && tp != "" {
            return tp;
        }
    }
    ()
}

// Basic-AttrGroup 은 25분마다 풀덤프, 상태변화시엔 {battery,locked,...} 델타만 옴 — 덮어쓰면 델타 한
// 번에 풀덤프 필드가 사라지므로 data 만 필드단위 병합(새 값 우선). 헤더는 항상 최신 메시지 그대로.
fn retain_merge(prev, next) {
    let out = next;
    if type_of(prev.data) == "map" && type_of(next.data) == "map" {
        let d = prev.data;
        d.mixin(next.data);
        out.data = d;
    }
    out
}

// 주입 대상(inject_target) 은 미정의 — core 기본값(토픽 세그먼트 중 연결된 기기 id = ocp/{sId}/{tpId}
// 의 tpId)으로 충분.

// ═══════════ 정책 훅(shadow/decloud 만) ═══════════

// 락→클라우드 Q(021=IDPEVENT류) 는 항상 로컬에서 200 ack 로 선응답(health 무관 — 서버가 살아있어도
// 먼저 답하고, 서버의 늦은 중복은 policy 쪽 dedup 이 알아서 버림).
fn on_message(msg, topic, health) {
    if msg.msgType == "Q" && msg.funcType == "021" {
        return #{
            topic: `ocp/${msg.sId}/${msg.tpId}`,
            qos: 1,
            json: #{
                version: msg.version,
                msgType: "A",
                funcType: msg.funcType,
                sId: msg.sId,
                tpId: msg.tpId,
                tId: msg.tId,
                msgCode: "MSGBA0210002",
                msgId: msg.msgId,
                msgDate: now_ms(),
                resCode: "200",
                resMsg: "",
                dataFormat: "application/json",
                severity: "0",
                encType: "0",
                authToken: msg.authToken
            }
        };
    }
    ()
}

// replay = 클라우드가 락 재접속 때 다시 보내주는 폰 e-key(NFC) 키싱크를 흉내(다음 로컬 부트스트랩 때 재생).
// ★MSGBA0300001 은 e-key 등록(408)만이 아니라 407(열림!)·410(슬롯 삭제)·414(비번 중복검사)·
// 420(토큰 삭제)·402·508 도 같은 msgCode 로 옴 — 반드시 func 로 좁힐 것(예전 "keysync//" 열림재생 버그).
// - 저장: 이름 있는 NFC 408 만, 키 = pin 토큰(같은 이름 재발급이면 토큰이 달라 별개 항목 → 옛 토큰은 420 으로
//   지워짐, 실측). 비밀번호(NUM) 등 락이 스스로 보관하는 키는 재생할 이유가 없고, 저장하면 평문 비밀번호가
//   디스크에 남고 지운 키가 장애 후 부활할 수 있음.
// - 삭제: 420(arg.pin = 토큰 → 키 바로 계산, 세션 무관) / 410(arg.pinId 슬롯배열 → ctx 의 pinSlot_N →
//   nfcTok_<이름> 으로 토큰을 찾음, 같은 세션에서 배운 경우만).
fn on_cloud_push(msg, topic, ctx) {
    let a = nfc_ekey(msg);
    if a != () {
        return `keysync/NFC/${a.pin}`;
    }
    if msg.msgCode != "MSGBA0300001" || type_of(msg.data) != "map" || type_of(msg.data.arg) != "map" { return (); }
    let f = msg.data.func;
    let arg = msg.data.arg;
    if f == 420 && type_of(arg.pin) == "string" && arg.pin != "" {
        return #{ forget: `keysync/NFC/${arg.pin}` };
    }
    if f == 410 && type_of(arg.pinId) == "array" {
        let keys = [];
        for id in arg.pinId {
            let slot = ctx[`pinSlot_${id}`];
            if type_of(slot) == "string" && slot.starts_with("NFC/") {
                let tok = ctx[`nfcTok_${slot.sub_string(4)}`];
                if type_of(tok) == "string" && tok != "" { keys.push(`keysync/NFC/${tok}`); }
            }
        }
        if keys.len() > 0 { return #{ forget: keys }; }
    }
    ()
}

// 관찰자(observer)가 로컬로 주입한 "열림" 명령(func=407,locked=false) 은 클라우드 방향으로도
// IDPEVENT 형태로 미러링해서 공식 앱 이력이 안 끊기게 함(shadow 전용 — proxy 는 정책 off,
// decloud 는 업스트림 없음). authToken 은 learn() 이 기억해둔 클라우드 발급 토큰.
fn on_local_inject(msg, topic, ctx) {
    let tok = ctx.cloudAuthToken;
    if type_of(tok) != "string" { tok = ""; }
    if msg.data.func == 407 && msg.data.arg.locked == false {
        return #{
            topic: "ocp/dataBus",
            qos: 1,
            json: #{
                version: "001",
                msgType: "Q",
                funcType: "021",
                sId: msg.sId,
                tpId: msg.tpId,
                tId: msg.tId,
                msgCode: "IDPEVENT",
                msgId: uuid(),
                msgDate: now_ms(),
                resCode: "",
                dataFormat: "application/json",
                severity: "0",
                encType: "0",
                authToken: tok,
                data: #{
                    locked: false,
                    access: "RFC",
                    pinId: 0,
                    pin: "",
                    count: 0,
                    msgCategory: 622,
                    testing: false,
                    mode: 0,
                    zoneId: 1,
                    eventId: `${now_ms()}622`,
                    from: ""
                }
            }
        };
    }
    ()
}

// Local(cloudless) 세션이 SUBSCRIBE 직후 authToken 발급 부트스트랩.
fn on_session_start(ctx) {
    #{
        topic: `ocp/${ctx.sId}/${ctx.tpId}`,
        qos: 1,
        json: #{
            version: "001",
            msgType: "A",
            funcType: "002",
            sId: ctx.sId,
            tpId: ctx.tpId,
            tId: ctx.tId,
            msgCode: "MSGAUTH00003",
            msgId: uuid(),
            msgDate: now_ms(),
            resCode: "200",
            resMsg: "",
            dataFormat: "application/json",
            severity: "0",
            encType: "0",
            authToken: ctx.authToken,
            data: #{ authToken: ctx.authToken, encType: "0" }
        }
    }
}
