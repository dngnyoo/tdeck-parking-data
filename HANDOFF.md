# T-DECK 인수인계 — 2026-10-30

`feat/notification-freshness-gate-20261030`은 최신 운영 main에서 분리한 준비 브랜치다.
기존 docs/development-handoff-20261024의 개발 노트와 지침을 보존해 함께 가져왔다.

- notification_gate/에 Python 검사·코드 보고서·중복 보호·테스트와 상세 한국어 보고서가 있다.
- 실제 Oracle Cron 5개와 최근 주차 알림을 조사했다. Cron 1200초와 공통 지침 1800초 충돌을 확인했다.
- 서버 비공개 백업과 독립 staging만 만들었다. 기존 Cron payload·AGENTS 지침은 **미변경**.
- 수집 Actions, CSV, 예측기, Pages, Gateway, 다른 자동화, 인증 및 서버 세션은 변경하지 않았다.
- 로컬 및 Oracle에서 발송 없는 테스트를 수행했다. 실제 모델 호출/Telegram 발송은 0회.
- 다음 단계: 사용자의 운영 승인 후 기존 5개 Cron payload만 command로 변경하고 T Deck 공통 지침을
  설정 기준으로 통일한다. 실제 알림 1회 검증은 별도 명시 승인이 필요하다. 반복 테스트 발송 금지.
- 기존 owner/sessionKey/current 설정과 수신 대상은 보존한다. command 결과의 대화 표시 차이는
  상세 보고서 한계에 기록했으므로 실제 검증 전 동등하다고 주장하지 않는다.
- 전체 프로젝트 이전 검증은 계속 보류다. 이번 알림 모듈 테스트만 수행했다.

자세한 구조·실측 JSON·정책·적용 및 복구 계획은 [보고서](notification_gate/README.md)를 읽는다.
