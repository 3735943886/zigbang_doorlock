// 직방 도어락 응답규칙(default/fallback)
// 파일명 99- 라서 다른 규칙파일들보다 항상 나중에 검사됨(우선순위 최저).

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

// 클라우드→락 keysync push 는 replay 저장(다음 로컬 부트스트랩 때 재생용).
fn on_cloud_push(msg, topic) {
    if msg.msgCode == "MSGBA0300001" {
        return `keysync/${msg.data.arg.pinType}/${msg.data.arg.pinName}`;
    }
    ()
}

// 관찰자(observer)가 로컬로 주입한 "열림" 명령(func=407,locked=false) 은 클라우드 방향으로도
// IDPEVENT 형태로 미러링해서 공식 앱 이력이 안 끊기게 함(proxy/shadow 전용, decloud 는 업스트림 없음).
fn on_local_inject(msg, topic) {
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
                authToken: msg.authToken,
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
