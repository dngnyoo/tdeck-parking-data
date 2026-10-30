# T-DECK 인수인계 — 2026-10-30

`feat/notification-freshness-gate-20261030`은 운영 main에서 분리한 알림 모듈 소스 브랜치다.
기존 docs/development-handoff-20261024의 개발 노트와 지침을 보존해 함께 가져왔다.

- notification_gate/에 Python 검사·코드 보고서·중복 보호·테스트와 상세 한국어 보고서가 있다.
- 실제 Oracle Cron 5개와 최근 주차 알림을 조사했다. Cron 1200초와 공통 지침 1800초 충돌을 확인했다.
- 사용자가 5개 유지와 운영 적용을 승인했다. 기존5개 payload를 command로 변경하고
  T Deck AGENTS 지침의 1800초 문장만 코드 설정 참조로 바꿨다. 비공개 원본/적용 백업이 있다.
- 수집 Actions, CSV, 예측기, Pages, Gateway, 다른 자동화, 인증 및 서버 세션은 변경하지 않았다.
- 로컬/Oracle 테스트 각28개 통과. 이후 승인된 실제 금요일 Cron 1회 검증에서
  Telegram delivered, execution ok, completion succeeded를 확인했다. 모델 호출0회.
- 5개 Cron의 시간·owner/sessionKey/current·수신 대상 및 다른5개 자동화 설정 보존을 확인했다.
  Gateway 재시작은 없었다. 추가 실제 발송은 하지 않는다.
- command 결과는 Cron 기록과 Telegram에 확인됐다. 일반 메인 대화의 agentTurn transcript와
  같은 방식의 본문 표시를 보장하지 않는다. 상세 보고서의 전달 큐·누락/중복 절충 한계를 읽는다.
- 다음: 정규 요일별 실행 기록을 관찰한다. 문제 시 원본 payload만 관리 API로 복구하고
  해당 지침 문장만 원복한다. SQLite 전체/세션 데이터를 덮어쓰거나 dedup DB를 지우지 않는다.
- 전체 프로젝트 이전 검증은 계속 보류다. 이번 알림 모듈 테스트만 수행했다.

자세한 구조·실측 JSON·정책·적용 및 복구 계획은 [보고서](notification_gate/README.md)를 읽는다.
