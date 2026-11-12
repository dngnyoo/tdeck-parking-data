# 온라인 학습 Phase B 구현 결과

수정 승인된 clipping 정책으로 구현했다. **온라인 모드는 off**이며, main 병합,
운영 활성화, 상태 브랜치 생성 또는 Pages 배포는 수행하지 않았다. 계수/경고
기준의 운영 교체도 없다. 원본 CSV·수집 코드·수집 workflow·사이트 소스는 그대로다.

## 구현 범위

* `observations.py`: 구조 검증, 원본 중복 중앙값, 유한한 잔여 대수의 무조건
  `[0,1300]` clipping. 큰 음수/양수와 양수 보고 수용량 차이는 제외 사유가 아니다.
* `online_model.py`: 날짜별 평균·마지막 관측시각 가중을 보존한 정확한 증분
  기준선, 현재 날짜 분리, 달력 비교 기준선, 만기 정답 기반 horizon별 온라인
  최소제곱, 기존 alpha 초기값/부족 표본 fallback, 예측 당시 특징 보존.
* `online_ingest.py`: 새 완성 행만 파싱, 처리 prefix 검증, 중복·역순·미완성 행
  처리, 과거 수정/완료 날짜 지연 관측/미래 센서 시각 감지 시 저장 중단.
* `online_state.py`와 JSON schema: checksum·설정 fingerprint·버전·범위 검증,
  배타 잠금·원자 교체·정상 이전 파일 복구. 불명 스키마는 추측하지 않는다.
* `state_remote.py`: 미래 활성화 시 데이터만 담는 model-state 브랜치 사용,
  기존 파일 SHA를 조건으로 저장, 충돌 시 덮어쓰지 않고 실패, 최근 정상 이력 복구.
* 별도 bootstrap/update/증분 export/Phase C 평가 CLI, 복구·rollback 문서.

예측 workflow의 기존 trigger, 동시성 설정과 deploy job은 유지했다. 기본 off에서
상태 작업은 실행되지 않는다. 향후 shadow로 승인해도 운영 `predictions`는 고정
alpha M3이고 온라인 결과는 별도 `online_shadow_predictions`에만 들어간다.
학습·원격 저장·선택적 캐시 export 실패 시 고정 M3로 fallback한다. 새 배포 환경,
승인 보호 규칙, 데이터베이스 또는 workflow 재호출은 추가하지 않았다.

## 실제 실행한 구현 검증

스냅샷 기반 커밋: `34f898d23135370c1f61426c5843ce280f3d30ed`.
원본 작업 파일 SHA256(CRLF): `c9037a61f47d21613096f61b798e6144a355a95f054fa4b0397ce9f4c62f03e6`.
체크포인트용 LF 표현 SHA256: `4ef0c6f914d61015fc619417704ec1ba9966666e4683fc4ec65a9ec305fd4820`.
관측 기간: 2026-09-03~2026-11-11, 20,001건. Python 3.12.14, 로컬 Windows에서
실행했다. 자세한 수치는 [기계 판독 검증 결과](phase_b_verification.json)에 있다.

| 검사 | 실제 결과 |
|---|---:|
| Python 테스트 | 38개 통과 |
| 프런트엔드 테스트 | 8개 통과 |
| 1회 로컬 bootstrap | 약 30.58초 |
| 생성된 상태 파일 | 761,811바이트 |
| 기준선 bucket | 14,402개 |
| 남은 미결 예측 | 31개 |
| 기존 배치/증분 고정 M3 최대 차이 | 약 2.84e-13대 |
| 달력 비교 예측 최대 차이 | 약 2.84e-13대 |
| 동일 입력 복원·검증·재처리 중앙값, 5회 | 약 0.105초 |
| 합성 관측 1개 증분 반영·원자 저장 | 약 0.207초 |
| 합성 추가분에서 파싱한 행 | 1개, 정규 표현 39바이트 |

큰 음수를 포함한 329건 모두 0으로 보정했다. 1,300 초과 원본은 이번 스냅샷에
없으며, 큰 양수 사례는 테스트로 검증했다. 보고 수용량이 다른 2,312건도 비례
환산·제외 없이 수용했다. 구조 오류는 0건이었다. 같은 파일 재처리는 학습 건수와
상태를 바꾸지 않았다. 합성 관측은 별도 로컬 복사본에만 추가했으며 원본 CSV
해시는 전후 동일하다. 위 시간은 로컬 구현 검증값으로 Actions 네트워크/배포
시간을 포함하지 않는다. prefix 해시 확인은 파일 전체 바이트를 읽지만, 정상
증분 갱신에서 과거 CSV를 다시 파싱하거나 학습하지 않는다.

bootstrap 후 후보 alpha는 15/30/60/120분에서 약
`0.9935 / 0.9864 / 0.9711 / 0.9264`였다. 이 값은 코드 작동을 보여주는
**미검증 후보**이며, 기존 `1 / 1 / 1 / 0.75`보다 좋다는 뜻이 아니다.
로컬 상태는 `.online-artifacts/phase-b-state-v1.json`에만 있고 원격에 게시하지 않았다.

테스트는 clipping/구조 오류, 날짜별 배치 동등성, 동일 입력 멱등성, 분할 재생과
재시작, 중복 중앙값과 열린 날짜 역순, 완료 날짜 지연 관측, CSV 수정/절단/미완성
행, 미래 센서와 과거 질의 차단, DST 반복 시각·자정 순환 창, 지연 정답과 공백,
미래 suffix 변경에 대한 이전 예측 불변, 계수 범위와 극단 정답, 원자 저장 실패,
손상/불명 정책 복구, 원격 충돌/정상 이력 복구를 포함한다. 원격 GitHub 쓰기는
모의 API로 검증했으며 실제 상태 브랜치 초기화/갱신은 실행하지 않았다.
잘못된 캐시에서도 CLI가 고정 M3 JSON을 생성하는 경로와 off 상태에서 상태 파일을
만들지 않는 경로도 검사했다. YAML을 파싱하고 기존 trigger·동시성·deploy job
내용이 바뀌지 않았음을 대조했다.

Windows checkout의 CRLF와 Linux checkout의 LF 때문에 상태를 재사용하지 못하는
문제를 방지하기 위해, 체크포인트 offset/해시는 UTF-8 LF 표현으로 계산한다.
앞쪽 BOM도 이 표현에서만 무시한다. 실제 CSV를 변환하거나 덮어쓰지 않으며,
공개 `source_sha256`은 여전히 실제 파일 바이트를 가리킨다. 줄바꿈/BOM을 바꿔도
재학습하지 않고, 실제 과거 값 수정은 감지하는 테스트가 통과했다.

## Phase C에서 남은 검증

MAE/RMSE/편향·만차/경고 precision/recall/F1, 날짜·시간대 성능, 계수 사전 강도와
망각 반감기·상한 민감도를 정식 비교해야 한다. 평가 CLI는 구현하고 합성 자료로
검사했지만 이번 Phase B에서 최종 테스트 구간 성능으로 설정을 선택하지 않았다.

비교군은 구정책 고정 M3, 신정책 고정 M3, 신정책 배치 기준선+온라인 alpha,
신정책 증분 기준선+같은 온라인 alpha다. 마지막 두 개는 정확도 차이가 아니라
계산 동등성/효율 비교다. `available`과 동일 origin `common` 집계로 clipping
정책이 바꾼 평가 대상과 학습이 바꾼 오차를 분리한다. 정답은 신정책에서 동일하게
보정하고, 원본 정답·실제 매칭 시각·매칭 오차도 남긴다.

정식 비교 후 검토·명시 승인 전까지 online mode는 off다. 향후 shadow 승인도
온라인 alpha의 운영 승격과는 별개다. 배포 안정성의 실제 원격 통합 검증은 운영
설정을 변경하지 않는 범위에서 계획한 뒤 Phase D 승인에 맞춰 수행해야 한다.

## 재현

저장소 루트에서 다음을 실행한다. Windows 기본 Python에 시간대 자료가 없으면
`python -m pip install tzdata`가 필요하다. 이미 출력이 있으면 새 경로를 쓰거나
bootstrap의 `--replace`를 명시한다.

```sh
python -m unittest discover -s model/phase3/tests -v
node --test model/phase3/tests/state.test.mjs
python model/phase3/bootstrap_online.py --state .online-artifacts/bootstrap.json
python model/phase3/update_online.py --state .online-artifacts/bootstrap.json --offline
python model/phase3/verify_phase_b.py --state .online-artifacts/bootstrap.json --out .online-artifacts/verification.json
```

자세한 구조, Phase C 명령, 상태 브랜치 계약, 스키마 변경 및 rollback은
[구현 안내](../../model/phase3/ONLINE_LEARNING.md)에 있다.
