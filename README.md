# T Deck Parking Outlook — Phase 3

기존 수집을 유지하면서 현재 관측과 15·30·60·120분 M3 예측을 표시하는 GitHub Pages 정적 서비스입니다.

## 변경 범위

`collect.py`, `.github/workflows/collect.yml`, `data/parking.csv`는 수정하지 않았습니다. 추가한 `.github/workflows/predict-pages.yml`은 수집 workflow `Collect T Deck parking data`가 main에서 성공한 뒤 실행됩니다. 수동 실행, 화면/모델 코드 변경, 15분 보조 일정도 지원합니다. GitHub 일정은 지연될 수 있으므로 정확한 5분 실행을 보장하지 않습니다.

예측 workflow는 저장소를 **읽기만** 하고 site/data/prediction.json을 실행 공간에서 생성합니다. JSON과 화면을 Pages artifact로 함께 배포하며 git commit/push를 하지 않습니다. 수집 commit과 경합하거나 자체 commit을 통해 수집을 재실행하지 않습니다. 실패한 수집 완료 이벤트는 예측 실행을 건너뜁니다. 기존 CSV가 20분보다 오래됐으면 stale JSON을 배포하고 예측을 비웁니다. 예측 workflow 자체가 실패하더라도 브라우저가 기존 JSON의 관측 시각을 매 15초 재검사해 만료 예측을 숨깁니다.

## 첫 배포

1. 이 변경 파일을 저장소 main에 반영합니다. 기존 수집 파일은 그대로 둡니다.
2. GitHub 저장소 **Settings → Pages → Build and deployment → Source: GitHub Actions**를 선택합니다. 새 workflow가 기본 branch에 있어야 workflow_run/수동 실행을 사용할 수 있습니다.
3. **Actions → Predict T Deck and deploy Pages → Run workflow**를 실행합니다. 또는 다음 수집 workflow 성공을 기다립니다.
4. 성공한 deploy job에 표시되는 Pages URL을 확인합니다. 통상 주소는 `https://dngnyoo.github.io/tdeck-parking-data/`이지만 실제 배포 성공 여부와 설정을 확인하기 전에는 공개 완료로 간주하지 않습니다.

GitHub Pages 환경에서 main 배포를 허용해야 합니다. 저장소의 Actions/Pages 정책 또는 환경 승인이 있으면 관리자 설정에 따라 배포가 대기할 수 있습니다. 별도의 PAT/API 비밀키가 필요하지 않으며 deploy job만 pages:write와 id-token:write를 사용합니다. collector의 contents:write는 변경하지 않았습니다.

## 로컬 실행

Python 3.12, Node.js 18 이상이면 됩니다. 예측 엔진은 Python 표준 라이브러리만 사용하므로 매 수집마다 분석 패키지를 설치하지 않습니다. Windows에서는 IANA timezone 자료가 필요할 때 `python -m pip install tzdata`를 한 번 실행하세요.

```sh
python -m unittest discover -s model/phase3/tests -v
node --test model/phase3/tests/state.test.mjs
python model/phase3/export_prediction.py
python -m http.server 8765 --directory site
```

브라우저에서 `http://localhost:8765`를 엽니다. `file://`로 열면 JSON fetch가 차단될 수 있습니다. `site/data/prediction.json`은 자동 생성 파일이라 commit 대상에서 제외했습니다. CSV가 과거 자료이면 예측이 숨겨지는 것이 정상입니다. 과거 snapshot 산식 검증에만 `--now "2026-10-13T15:30:16-04:00"`을 지정할 수 있습니다. 배포 workflow는 `--now`를 쓰지 않습니다.

## 고정 모델과 서비스 정책

- Phase 2에서 validation으로 선택한 M3만 운영합니다. completed_local_days 기준선 반감기 7일, ±15분 창, 날짜별 평균 후 가중 평균. 당일 자료를 B(t)/B(t+h) 계산에 쓰지 않습니다.
- `prediction = B(t+h) + alpha × (current − B(t))`. alpha는 15/30/60분에서 1.0, 120분에서 .75입니다. 경고 cutoff는 25/25/50/100면입니다. 설정은 `model/phase3/config.json`에 보존했습니다.
- 이 설정은 2026-09-24부터 사용 가능합니다. 라이브 자료로 다시 tuning하지 않습니다. 추가 학습 없이 매 실행 시 이전 날짜의 기록만 기준선에 포함합니다.
- current/예측의 음수는 보존합니다. 음수는 잔여 공간 없음 또는 초과 집계일 수 있으며 실제 수용량 초과를 확정하지 않습니다.
- 15/30분 전 실제 관측 ±90초 매칭 및 최근 이력 gap<=600초 조건은 Phase 2 평가와 동일하게 유지했습니다. 최근 이력이 부족하면 M3 계산이 가능하더라도 검증 범위 밖으로 취급해 예측을 숨깁니다.
- 현재 관측이 20분보다 오래됨, 센서 시각이 2분 이상 미래, 수용량 변경/잔여 공간 수용량 초과, 과거 날짜 14개 미만이면 예측을 숨깁니다. 원본 CSV를 보간·수정하지 않습니다.
- 화면은 한국어, 모든 시각은 America/New_York. 현재 관측은 항상 시각과 함께 표시하고, 예측과 실제값을 구분합니다. 1분마다 새 JSON 확인, 탭 복귀 시 재확인, 새로고침 버튼을 제공합니다.
- `free_spaces<=0`와 혼잡 경고를 별도 계산합니다. warning은 확정 만차나 확률이 아닙니다. 그래프는 관측 공백을 끊고, 미래 네 예측점은 점선으로 표시합니다. 그래프 수치 표와 키보드 접근을 제공합니다.

## JSON v3

`site/data/prediction.json`에 schema_version, status/reason, generated_at, forecast_origin, valid_until, freshness_seconds, current, history(최근 3시간), predictions(네 horizon), source_revision, CSV SHA256를 포함합니다. status=ok일 때만 forecasts를 표시합니다. stale/unavailable은 빈 predictions이며 마지막 관측은 과거 기록으로 표시할 수 있습니다.

JSON의 관측 origin을 기준으로 신선도를 판단하며, 새 배포 시각만으로 과거 자료가 새 자료처럼 보이지 않습니다. 데이터 파일은 CSV만 읽습니다. 프런트엔드에는 GitHub credential, API key, workflow 환경 정보가 포함되지 않습니다.

## 검증과 한계

Python tests: 음수·읽기전용 CSV·20분 만료 경계·미래 센서·관측 gap·선택 시각 이전·중복/invalid·기준선·수용량 이상. Node tests: 정상/음수, 만료, 네트워크 실패, 미래 시각, 서버 오류, 잘못된 horizon/JSON. 로컬에서 모바일 및 넓은 화면도 검증했습니다.

별도 Phase 3 parity 검증에서 표준 라이브러리 추론이 기존 Phase 2 NumPy/Pandas 구현과 일치하는지 확인했습니다. 이는 새 모델의 성능 실험이 아니라 같은 선택 모델의 구현 일치 확인입니다. 분석 결과의 MAE는 과거 테스트이며 미래 보장값이 아닙니다. 만차 평가 가능한 사건이 두 개에 불과해 서비스는 비공식 실험용입니다.

## 공식 workflow 참고

- [GitHub Pages custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [workflow_run / schedule event rules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
