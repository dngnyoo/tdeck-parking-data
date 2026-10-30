# T Deck 알림 신선도 게이트 — 운영 승인 대기

2026-10-30. 이 모듈은 독립 준비 폴더에서 검증했으며 **운영 Cron에는 연결하지 않았다**.
모델 호출과 실제 Telegram 테스트 발송은 0회다. 서버의 실제 수신 대상·Cron 원본은
서버의 권한 제한 백업에만 보관하며 공개 저장소에는 넣지 않는다.

## 확인된 구조와 원인

- Oracle OpenClaw 2026.9.6 (`eb377ac`)의 Gateway 내장 Cron이다. 사용자 crontab은 없다.
- 주차 작업 5개는 `agentTurn`, `agentId=main`, `sessionTarget=current`,
  `sessionKey=agent:main:main`, `toolsAllow=[web_fetch]`를 사용한다.
- 시간대는 모두 America/New_York. 월 11:30 / 화 09:30 / 수 11:30 / 목 11:45 / 금 10:00.
  만차일 때만 보내는 조건이 아니라 첫 일정 1시간 전 현재 잔여면과 60분 예측을 보고하는 작업이다.
- 데이터는 `https://dngnyoo.github.io/tdeck-parking-data/data/prediction.json`.
  수집 API → `collect.py` → CSV → `export_prediction.py` → Actions Pages artifact → JSON이다.
  JSON은 Git에 커밋하지 않고 Pages에서 배포한다.
- `schema_version=3`, `status`, `current.timestamp`, `forecast_origin`, `generated_at`,
  `current.free_spaces`, `current.total_spaces`, `predictions`가 실제 필드다.
  이 JSON에는 `observed_at`이 없다. 게이트 출력의 `observed_at`은 current.timestamp를 UTC로 정규화한 값이다.
- `model/phase3/config.json`의 `freshness_seconds=1200`은 **허용 한도**다.
  export_prediction.py는 UTC 경과를 계산하며 정확히 1200초는 허용한다.
  collector의 timestamp는 API를 조회한 시각이며 원 센서의 별도 측정 시각을 증명하지는 않는다.
- 5개 Cron 프롬프트 역시 최대 1200초를 지시하지만 서버 공통 `workspace/AGENTS.md`의
  T Deck 항목은 1800초라고 되어 있다. 공통 지침은 아직 수정하지 않았다.
- 2026-10-30 금요일 10:00 실행 기록은 09:55 관측을 오래된 데이터로 보고했다.
  프롬프트에 검증 규칙은 있어도 코드 게이트가 없고 LLM이 해석·판정한다.
  금요일 run의 모델은 gpt-6-luna였다. 이는 증상의 직접 증거이며 내부 추론 원문을 확인한 것은 아니다.
- Telegram은 별도 메시지 도구 호출 없이 `delivery.mode=announce`, 명시된 기존 DM 대상으로
  Gateway가 최종 결과를 전달한다. 기존 주차 작업에 `trigger`나 관측 내용 기반 dedup은 없다.
  OpenClaw는 current-agent 결과의 job/run 멱등성과 채널의 모호한 전송 재시도 억제를 제공하지만,
  동일 관측을 다른 수동 실행으로 재발송하는 것을 이것만으로 막지는 못한다.

## 가장 작은 운영 변경안

기존 5개 Cron의 **payload만 command로 교체**한다. ID, 이름, 스케줄, timezone,
owner, sessionKey, sessionTarget, Telegram 수신 대상, failureAlert는 그대로 유지한다.
별도 systemd 스케줄러·Gateway 재시작·GitHub Actions 변경은 필요 없다.

```text
기존 Cron → python -m notification_gate.command --emit ...
          → 공개 JSON 조회 → UTC 신선도·구조 검사
          → fresh인 경우 중복 예약 → 코드로 한국어 보고서 작성
          → 발송 인계 직전 다시 검사 → 기존 Gateway announce → Telegram
```

고정 형식 보고서에는 모델이 필요하지 않아 **운영안의 LLM 호출은 0회**다.
`Gate.run()`의 model callback은 선택적 연결용 인터페이스이며 command.py에서는
로컬 `report()` 함수만 호출한다. 외부 에이전트를 그 callback에 연결하는 변경은 이번 운영안에 없다.
따라서 모델이 검사를 생략하거나 차단을 뒤집는 경로가 없다.

설치된 소스에서 검증한 계약:

- `dist/jobs-PNxRqKpe.mjs:41` 부근 `assertSupportedJobSpec()`은 command/current를 허용한다.
- `dist/server-cron-Dd6AX5Mc.mjs:212` `runCronCommandJob()`은 모델 없이 프로세스를 실행한다.
- 같은 파일 `:3183` runCommandJob은 정확한 `NO_REPLY`를 silent로 억제한다.
- `:2760` `finalizeCronCompletionAnnouncement()`는 기존 전달기·session key를 사용한다.
  명령 실행 결과는 Cron run history에 남는다. 기존 agentTurn 대화 transcript와 같은 형태의
  본문 추가까지 보장하는 것은 아니므로 승인 후 실제 세션 표시를 별도 확인해야 한다.

## 판정·중복 정책

- `age_seconds = UTC(now) - UTC(current.timestamp)`.
- 기준은 로컬 신뢰 설정 `model/phase3/config.json` 한 곳에서 읽는다.
  원격 JSON의 freshness_seconds를 실제 나이나 새로운 허용 한도로 사용하지 않는다.
- `0 <= age_seconds <= 1200`이고 schema/current/source status가 유효하면 fresh.
- 1200초 초과는 stale. 모든 미래 관측은 invalid로 엄격하게 차단한다.
  기존 예측기 120초 미래 허용보다 보수적이며 예측기 자체는 변경하지 않았다.
- naive/없는/불가능한 시각, 서로 다른 origin/current, 미래 생성시각, 잘못된 수치는 invalid.
  API/JSON 조회 실패 또는 source status가 ok가 아니면 unavailable.
  올바른 형식의 잘못된 시계 값까지 이 정보만으로 완벽히 탐지할 수는 없다.
- fresh가 아니면 본문은 NO_REPLY. API/invalid/unavailable은 오류 종료하여 기존 failureAlert
  정책이 다룬다. 일반 주차 본문과 진단성 실패 알림은 구분한다. stale은 조용히 종료한다.
- identity = 주차장 + UTC 관측 시각 + 잔여면 + 총면의 SHA256.
  배포 시각·Git revision·무작위 run ID는 제외한다. 같은 시각이라도 관측 수치가 바뀌면 새 identity다.
- SQLite UNIQUE 예약을 모델/보고서 작성 **전**에 커밋한다. scope는 기존 Cron ID로 고정한다.
  프로세스 중단·모호한 전송은 자동 재시도하지 않는다. 새 관측은 계속 허용한다.
- `handed_off`는 stdout을 Gateway에 인계했다는 뜻이며 Telegram 수신 확인이 아니다.
  실제 성공은 OpenClaw run history의 deliveryStatus로 확인해야 한다.
  예약 직후 프로세스가 죽으면 알림이 누락될 수 있다. 중복 억제를 우선한 at-most-once 정책이며,
  재전송하려면 run/provider 결과를 먼저 수동 확인해야 한다. exactly-once를 주장하지 않는다.
- 발송 직전 검사는 **native announce에 인계 직전**이다. 이후 Gateway/Telegram 큐 지연까지
  신선도를 다시 검사하는 기능은 없다. 전송 시점에도 절대 만료를 보장하려면 전달기 변경이 필요하다.

## 실행과 검증

Python 3.12+ 표준 라이브러리. Ubuntu는 시스템 IANA timezone 사용.
Windows는 tzdata가 필요하다. 이번 Python 3.14 환경은 tzdata 누락으로 보고서 테스트 3개가
처음 실패했고, tzdata가 설치된 Python 3.13과 Oracle Python 3.12에서 검증했다.

```sh
python -m unittest discover -s notification_gate -v
python -m notification_gate.command  # 읽기 전용, JSON 출력, Telegram 호출 없음
```

운영 연결 예정 명령은 다음 형태다. 실제 경로와 기존 Cron ID는 서버 비공개 계획 파일에만 둔다.

```text
argv: ["/usr/bin/python3", "-m", "notification_gate.command", "--emit",
       "--database", "<private-state-db>", "--event-scope", "<existing-job-id>"]
cwd: <validated-staging-directory>
timeoutSeconds: 120
outputMaxBytes: 8192
```

28개 테스트: 5/19/정확히20/21분, 미래·시간대 누락/오류·DST·시각 충돌,
API/JSON 실패, 한도/나이 혼동 방지, 동일 관측·동시 실행·재배포·같은 시각의 수치 변경,
실패·프로세스 중단·작성 중 만료, 비정상 모델 결과, CLI NO_REPLY, 60분 예측 검증.
실제 모델·Telegram 호출은 테스트하지 않았다.

Oracle 읽기 전용 검사 실측 예:

```json
{"status":"fresh","reason":null,"age_seconds":55.761808,"max_age_seconds":1200,
 "observed_at":"2026-10-30T18:25:14+00:00","checked_at":"2026-10-30T18:26:09.761808+00:00",
 "allow_model":true,"allow_notification":true}
```

allow는 허용 판정이며 호출·발송 완료 표시가 아니다.

## 승인 후 적용·복구 순서

1. 기존 5개 작업과 workspace/AGENTS.md를 서버 mode 0700/0600 백업에 보관했다.
2. 적용 직전 최신 작업과 백업의 설정을 비교한다. 다른 변경이 있으면 병합 검토한다.
3. 운영 연결 승인 후, 기존 ID에 `openclaw gateway call cron.update`로 payload만 patch한다.
   다른 자동화·토큰·Gateway 설정·GitHub main은 변경하지 않는다.
4. workspace/AGENTS.md의 T Deck 1800초 문장만 단일 설정 참조로 교체한다.
5. 승인받은 **1회** 실제 실행·Telegram 수신·run history·세션 표시를 확인한다.
   실패 시 임의 반복 발송하지 않는다.
6. 문제가 있으면 동일 API로 백업의 payload만 복구하고 T Deck 지침 문장만 원복한다.
   현재 런타임 state, 다른 작업, 세션 DB를 백업 파일로 통째로 덮어쓰지 않는다.
   dedup DB는 삭제하지 않고 보존하며 재적용 시 전송 이력을 먼저 확인한다.

**현재 3~5단계는 미실행이다. 운영 적용 전 사용자 승인이 필요하다.**
