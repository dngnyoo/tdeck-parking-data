"""Generate the Korean Phase C report and scientific plots from frozen results."""
from collections import defaultdict
import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    with Path(path).open(encoding='utf-8',newline='') as stream:return list(csv.DictReader(stream))


def table(head,rows):
    return '| '+' | '.join(head)+' |\n|'+'|'.join(['---']*len(head))+'|\n'+'\n'.join('| '+' | '.join(map(str,row))+' |' for row in rows)


def num(value,digits=3):
    return '—' if value in [None,''] else f'{float(value):.{digits}f}'


def build(out,csv_path,source_commit,figures=True):
    out=Path(out)
    selection=json.loads((out/'selection.json').read_text())
    manifest=json.loads((out/'test_manifest.json').read_text())
    stable=json.loads((out/'stability.json').read_text())
    parity=json.loads((out/'full_baseline_parity.json').read_text())
    metrics=read(out/'test_metrics.csv');validation=read(out/'validation_metrics.csv')
    predictions=read(out/'test_predictions.csv');intervals=read(out/'paired_day_bootstrap.csv')
    def metric(cohort,model,h,kind='full',breakdown='all',group='all',data=None):
        return next(r for r in (data if data is not None else metrics) if r['cohort']==cohort and r['model']==model and int(r['horizon'])==h and r['warning_type']==kind and r['breakdown']==breakdown and r['group']==group)
    primary=[];clean=[];validation_rows=[];ci_rows=[];warning_rows=[];morning=[];legacy_rows=[]
    improvements={}
    for h in [15,30,60,120]:
        for cohort,output in [('all',primary),('clean',clean)]:
            a,b=metric(cohort,'fixed',h),metric(cohort,'online',h)
            pct=100*(float(b['mae'])/float(a['mae'])-1)
            output.append([h,a['n'],num(a['mae'])+' → '+num(b['mae']),num(a['rmse'])+' → '+num(b['rmse']),num(a['bias'])+' → '+num(b['bias']),f'{pct:+.2f}%'])
            if cohort=='all':improvements[str(h)]=dict(fixed_mae=float(a['mae']),online_mae=float(b['mae']),mae_change_percent=pct)
        a,b=metric('all','fixed',h,data=validation),metric('all','online',h,data=validation)
        validation_rows.append([h,a['n'],num(a['mae']),num(b['mae']),f"{100*(float(b['mae'])/float(a['mae'])-1):+.2f}%"])
        for kind in ['full','warning']:
            a,b=metric('all','fixed',h,kind),metric('all','online',h,kind)
            warning_rows.append([h,a['cutoff'],'/'.join(num(a[k]) for k in ['precision','recall','f1']),
                                 '/'.join(num(b[k]) for k in ['precision','recall','f1']),a['fp']+' → '+b['fp'],a['fn']+' → '+b['fn']])
        a,b=metric('all','fixed',h,breakdown='busy_weekday_morning',group='06-10'),metric('all','online',h,breakdown='busy_weekday_morning',group='06-10')
        morning.append([h,a['n'],num(a['mae']),num(b['mae']),f"{100*(float(b['mae'])/float(a['mae'])-1):+.2f}%"])
        a,b,c=metric('legacy_common','legacy_fixed',h),metric('legacy_common','fixed',h),metric('legacy_common','online',h)
        legacy_rows.append([h,a['n'],num(a['mae']),num(b['mae']),num(c['mae'])])
    for r in intervals:
        ci_rows.append([r['cohort'],r['horizon'],num(r['delta_mae_online_minus_fixed']),f"[{num(r['ci025'])}, {num(r['ci975'])}]",r['days_with_pairs']])
    periods=[];weekdays=[]
    for period in ['00-06','06-10','10-14','14-18','18-24']:
        a,b=metric('all','fixed',120,breakdown='time_of_day',group=period),metric('all','online',120,breakdown='time_of_day',group=period)
        periods.append([period,a['n'],num(a['mae']),num(b['mae'])])
    for d,label in enumerate(['월','화','수','목','금','토','일']):
        a,b=metric('all','fixed',120,breakdown='weekday',group=str(d)),metric('all','online',120,breakdown='weekday',group=str(d))
        weekdays.append([label,a['n'],num(a['mae']),num(b['mae'])])
    # Full-capacity observations and episodes are descriptive, not tuning inputs.
    source=read(csv_path)
    used=[r for r in source if r['timestamp'][:10]<='2026-11-11']
    test_raw=[r for r in used if '2026-10-29'<=r['timestamp'][:10]<='2026-11-11']
    full_rows=[r for r in test_raw if float(r['free_spaces'])<=0]
    episodes=[];active=None
    for r in test_raw:
        now=datetime.fromisoformat(r['timestamp'])
        if float(r['free_spaces'])<=0:
            if active is None or now.timestamp()-active['last_seconds']>600:
                active=dict(start=r['timestamp'],end=r['timestamp'],observations=0,last_seconds=now.timestamp());episodes.append(active)
            active.update(end=r['timestamp'],observations=active['observations']+1,last_seconds=now.timestamp())
        else:active=None
    for e in episodes:e.pop('last_seconds')
    audit=dict(source_commit=source_commit,source_rows=len(source),used_rows=len(used),test_rows=len(test_raw),
               used_start=used[0]['timestamp'],used_end=used[-1]['timestamp'],test_start=test_raw[0]['timestamp'],test_end=test_raw[-1]['timestamp'],
               test_negative=sum(float(r['free_spaces'])<0 for r in test_raw),test_exact_zero=sum(float(r['free_spaces'])==0 for r in test_raw),
               test_above_capacity=sum(float(r['free_spaces'])>1300 for r in test_raw),test_reported_capacity_mismatch=sum(float(r['total_spaces'])!=1300 for r in test_raw),
               full_dates=sorted({r['timestamp'][:10] for r in full_rows}),full_episodes=episodes,
               matched_clean_pairs=sum(r['clean']=='True' for r in predictions),matched_all_pairs=len(predictions))
    (out/'source_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    # Actual selected-setting execution must equal the cached optimizer replay.
    for h,optimizer in manifest['final_optimizers'].items():
        real=stable['final_optimizers'][str(h)]
        for field in ['alpha','sxx','sxz','pairs']:
            if abs(optimizer[field]-real[field])>1e-10:raise AssertionError('Real selected optimizer replay mismatch')
    daily=defaultdict(lambda:[0.,0.,0])
    for r in predictions:
        key=(r['origin'][:10],int(r['h']))
        item=daily[key];item[0]+=abs(float(r['fixed'])-float(r['actual']));item[1]+=abs(float(r['online'])-float(r['actual']));item[2]+=1
    from evaluate_online import write_csv, scores
    day_samples=defaultdict(list)
    for r in predictions:
        for cohort in ['all']+(['clean'] if r['clean']=='True' else ['clipping_involved']):
            for model in ['fixed','online']:
                day_samples[(cohort,model,int(r['h']),r['origin'][:10])].append(dict(prediction=float(r[model]),actual=float(r['actual'])))
    write_csv(out/'daily_metrics.csv',[dict(cohort=c,model=m,horizon=h,date=d,**scores(rows,0)) for (c,m,h,d),rows in sorted(day_samples.items())])
    wins={str(h):sum(v[1]<v[0] for (day,hh),v in daily.items() if hh==h) for h in [15,30,60,120]}
    mean_fixed=sum(improvements[str(h)]['fixed_mae'] for h in [15,30,60,120])/4
    mean_online=sum(improvements[str(h)]['online_mae'] for h in [15,30,60,120])/4
    summary=dict(question='Does online alpha improve fixed-alpha M3?',answer='Horizon-dependent: 120-minute MAE improves, 15/30/60-minute MAE worsens.',
                  per_horizon=improvements,equal_horizon_mean_mae_fixed=mean_fixed,equal_horizon_mean_mae_online=mean_online,
                  daily_online_mae_wins_out_of_14=wins,selected_options=selection['selected_options'],
                  clean_definition='Both raw origin and matched raw outcome within [0,1300]; training history still includes all normalized observations.',
                  recommended_next_step='Consider explicitly approved shadow observation focused on 120-minute forecasts; no operational promotion.',
                  online_mode='off',selection_frozen_commit='afcdf38da',final_test_used_for_selection=False,
                  no_production_or_pages_changes=True)
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    if figures:make_figures(out,predictions,metrics,intervals,daily)
    report=f'''# Phase C 오프라인 검증 결과

**현재 온라인 학습은 모든 horizon에서 더 정확하지 않다.** 고정 α M3 대비 최종 테스트의 120분 MAE는 **{abs(improvements['120']['mae_change_percent']):.2f}% 감소**했다. 반면 15·30·60분 MAE는 각각 **{improvements['15']['mae_change_percent']:.2f}%·{improvements['30']['mae_change_percent']:.2f}%·{improvements['60']['mae_change_percent']:.2f}% 증가**했다. clipping 없는 표본도 같은 방향이다. 따라서 전체 운영 교체는 권하지 않으며, 별도 승인을 전제로 120분 후보를 중심으로 shadow 관찰하는 것이 타당하다. **mode는 off이고 운영 모델·Pages 설정은 변경하지 않았다.**

## 1. 시간 분리와 비교 설계

소스 커밋 `{source_commit}`의 CSV를 별도 로컬 파일로 고정했다. 수집 파일 자체는 수정하지 않았다. 수집 스냅샷 {len(source):,}건 중 11월11일까지 **{len(used):,}건**만 재생했다. 테스트 원시 관측은 **{len(test_raw):,}건**, `{test_raw[0]['timestamp']}`~`{test_raw[-1]['timestamp']}`다. 이후 관측은 기준선·학습·정답 매칭에 사용하지 않았다.

* 검증: 10월15~28일. 그 이전 자료는 초기 기준선·온라인 학습 준비에 사용했다.
* 후보: 반감기7/14/28일 × 사전 강도50/200/800 × α 상한1.5/2/3, 총27개.
* 선택 기준: 전체 matched pair에서 네 horizon MAE의 동일 가중 평균. clean·경고·최종 테스트 점수로 설정을 고르지 않았다.
* 선택: **반감기14일, 사전 강도50, α 범위[0,2]**. 사전 특징 규모100대·최소20쌍 등 나머지는 고정했다. 상한1.5/2/3은 이번 검증에서 예측이 같아 사전에 정한 동률 규칙으로2를 택했다. 실질적으로 다른 점수는9개였다.
* 선택 파일과 프로토콜을 커밋 **`afcdf38da`**로 먼저 고정한 뒤 테스트를 평가했다. 검증 선택 objective는 **{selection['validation_objective']:.6f}대**다. 설정 간 점수 차이가 작아 유일한 최적 설정이라고 주장하지 않는다.
* 테스트: 10월29일~11월11일. 설정은 고정하고, 각 테스트 정답이 관측된 이후에만 α를 순차 갱신했다. 이는 매 시점 이전 자료로 다음을 예측하는 prequential 평가다.

운영 고정값은 15/30/60분 α=1, 120분 α=.75다. 모든 신정책 비교군은 동일한 유한값 clipping·동일 origin·동일 정답·동일 경고 cutoff를 사용한다. 기존 공개 모델의 수용량 거부 정책은 별도 대조군으로만 분리했다. ±90초 매칭 창이 닫히기 전에는 학습하지 않았고, 예측 당시 기준선·특징을 정답 도착 후 다시 계산하지 않았다.

검증 성능(고정 → 선택 후보):

{table(['분','n','고정 MAE','온라인 MAE','변화'],validation_rows)}

## 2. 최종 테스트: 전체 자료

편향은 `예측−정답`이며 양수는 빈자리를 과대 예측한 것이다. 단위는 주차 대수다.

{table(['분','동일 쌍 n','MAE 고정 → 온라인','RMSE 고정 → 온라인','편향 고정 → 온라인','MAE 변화'],primary)}

![전체·clean MAE](figures/01_mae_comparison.png)

네 horizon MAE를 동일 가중 평균하면 **{mean_fixed:.3f} → {mean_online:.3f}대**다. 이 평균 개선은 120분 결과가 주도한다. 온라인 최소제곱은 제곱오차를 학습하므로, 일부 큰 오차를 줄여 RMSE가 소폭 낮아져도 보통 표본의 MAE는 악화될 수 있다. 따라서 RMSE만으로 모든 horizon의 개선을 주장하면 안 된다. 날짜별 온라인 MAE 개선 일수는 15/30/60/120분에서 **{wins['15']}/{wins['30']}/{wins['60']}/{wins['120']}일**(각14일 중)이었다.

## 3. clipping 없는 표본

clean은 **origin 원본과 매칭된 정답 원본이 모두 [0,1300]인 쌍**이다. 같은 모델의 평가 표본만 나눈 것이며, 학습 이력에서 clipping 자료를 제거하고 재학습한 실험이 아니다. 전체 {len(predictions):,}쌍 중 clean은 {audit['matched_clean_pairs']:,}쌍이다. clipping 관련 쌍은 `test_metrics.csv`의 `clipping_involved`에 별도 보존했다.

{table(['분','clean n','MAE 고정 → 온라인','RMSE 고정 → 온라인','편향 고정 → 온라인','MAE 변화'],clean)}

120분 개선이 clipping 표본에만 의존하지 않는 반면, 단기 MAE 악화도 clean에서 남았다. 테스트 원시 관측에는 음수 **{audit['test_negative']}건**, 정확한0 **{audit['test_exact_zero']}건**, 1300초과 **{audit['test_above_capacity']}건**이 있었다. 보고 수용량 차이는 {audit['test_reported_capacity_mismatch']}건으로, 제외/비례 환산하지 않았다.

## 4. 날짜 단위 불확실성

같은 origin 날짜의 고정·온라인 오차를 함께 묶어14일을 복원추출하는 paired day-block bootstrap을2,000회 실행했다(seed20261112). 차이는 `온라인 MAE−고정 MAE`로 **음수가 개선**이다.

{table(['표본','분','MAE 차이','날짜 bootstrap 95% 구간','자료가 있는 날짜'],ci_rows)}

![날짜 bootstrap 차이](figures/02_paired_day_intervals.png)

이번14일 안에서는 단기 MAE 악화와120분 개선의 방향이 비교적 일관됐다. 다만14일은 짧고, 날짜 간 독립성·대표성이나 여러 비교를 보정한 확증적 유의성을 입증하지 않는다. 같은5분 기록과 겹치는 horizon을 독립 표본으로 세지 않았다. 다음 학기·휴일·다른 수요 국면으로 일반화할 근거는 없다.

## 5. 혼잡 경고와 만차

정답 사건은 **정규화된 API 잔여 대수<=0**이다. 실제 현장의 만차를 별도 센서로 확인한 정답은 아니다. cutoff0은 정확한 만차 예측, 25/25/50/100은 기존 혼잡 경고다. P/R/F1은 precision/recall/F1 순서이며 —는 분모가 없어 정의되지 않은 값이다. FP/FN은 겹치는 예측-정답 쌍 수다.

{table(['분','cutoff','고정 P/R/F1','온라인 P/R/F1','FP 고정 → 온라인','FN 고정 → 온라인'],warning_rows)}

120분 경고(cutoff100)는 recall을 유지하면서 FP를161→138로 줄였다. 하지만 FP는 여전히 많고, 정확한 만차(cutoff0)는 recall 상승과 함께 FP도6→9로 늘었다. 단기 경고 개선은 작거나 없었다. clean 표본에는 이번 테스트에서 만차 정답이 없어 만차 recall을 평가할 수 없다. `test_metrics.csv`에 undefined를 빈 값으로 남겼다.

원시 자료의 정규화 만차는 {len(audit['full_dates'])}개 날짜, 연속된0이하 기록이 양수 관측 또는10분 초과 gap으로 끊기는 정의에서 **{len(episodes)}개 구간**이었다. 같은 구간의 여러 예측을 독립적인 만차 사건으로 해석하지 않는다. clipping으로0이 된 음수 값에 경고 성능이 의존한다는 한계가 있다.

## 6. 요일·시간대

평일 오전06~10시:

{table(['분','n','고정 MAE','온라인 MAE','변화'],morning)}

120분 시간대별(모든 요일):

{table(['origin 현지 시간','n','고정 MAE','온라인 MAE'],periods)}

120분 요일별:

{table(['요일','n','고정 MAE','온라인 MAE'],weekdays)}

다른 horizon의 요일·시간대별 MAE/RMSE/편향·경고 지표도 CSV에 포함했다. 개별 요일은 약2일뿐이므로 특정 요일의 우열을 별도 모델 선택 기준으로 사용하지 않았다.

![실제값과 두 모델 예측 예시](figures/03_actual_vs_predicted.png)

## 7. 과거 정규화 정책과의 분리

아래는 구정책 고정 M3도 평가 가능한 동일 origin/정답에 한정한 MAE다. primary 결론은 신정책 고정 α와 온라인 α의 비교에서 얻었으며, clipping 변경 효과를 온라인 학습 효과로 섞지 않았다.

{table(['분','공통 n','구정책 고정','신정책 고정','신정책 온라인'],legacy_rows)}

## 8. 증분 계산·재시작·손상 안정성

최종 테스트 **{parity['checked_final_test_forecasts']:,}개 예측·{parity['checked_origins']:,}개 origin 전부**를 원래 배치 기준선과 대조했다. 날짜 cutoff/대상 요일·분이 같은 질의만 정확하게 재사용했고, 배치 질의는 {parity['exact_batch_queries']:,}회였다. 최대 기준선/특징 차이는 {parity['max_baseline_or_origin_feature_error']:.3g}대, 고정 예측 차이는 {parity['max_fixed_prediction_error']:.3g}대, 온라인 예측 차이는 {parity['max_online_prediction_error']:.3g}대였다. 차이는 부동소수점 수준이다.

후보27개는 α와 무관한 인과적 기준선/특징 및 정답 공개 이벤트를 재사용해 계산했다. 캐시 재생과 실제 Phase B 기본 엔진의 예측 최대 차이는 **0**이었다. 선택된 설정을 실제 엔진으로 다시 재생한 optimizer 상태도 캐시의 선택 후보와 일치했다. 따라서 후보 sweep만 별도 수학을 구현해 잘못 평가하는 위험을 점검했다.

실제20,083건의 상태 스트레스에서는 다음이 통과했다.

* 10월28일 이후 실행을 모두 놓쳤다고 가정해 테스트 신규4,068건을 한 번에 따라잡기.
* 12시간마다28회 상태 저장·프로세스 재시작 후 이어 처리하기.
* 최신 상태를 손상시킨 뒤 이전 정상 상태를 복원하고 신규 자료 재처리하기.
* 동일 파일5회 재실행: 신규 학습0건, 상태 불변.
* 현재/이전 상태 모두 손상: CLI 성공, status=ok, 네 고정 M3 예측을 정상 생성.
* 과거 CSV prefix 값 손상: 감지 후 상태 불변.

연속·재시작·손상 복구의 최종 상태 SHA256은 모두 `{stable['reference_state_sha256']}`로 같았다. 최종 상태 크기는 **{stable['final_state_bytes']:,}바이트**다. 테스트4,068건 한 번의 catch-up은 약 {stable['scenarios'][0]['seconds']:.2f}초, 동일 입력 복원/검증 중앙값은 약 {stable['scenarios'][3]['median_seconds']:.3f}초였다. Python/로컬 환경의 측정이며 GitHub 네트워크·배포 지연 성능 보장은 아니다. 상세 실제 수치는 `stability.json`, `full_baseline_parity.json`에 보존했다.

## 9. 판단과 다음 단계

**“온라인 학습이 기존 고정 α M3보다 실제로 더 정확한가?”에 대한 답은 “120분은 개선됐지만,15~60분의 MAE는 악화됐다”이다.** 전체 자동 교체는 권하지 않는다. 120분 이득은 clean에서도 유지됐고 날짜별 비교에서도 나타났으므로, 별도 승인 후 운영 예측을 바꾸지 않는 shadow 관찰 대상으로 삼을 근거는 있다. 단기 후보도 같은 shadow 기록에서 관찰하되 개선 모델이라고 부르면 안 된다.

최종 테스트를 본 뒤 horizon별 설정이나 α 정책을 다시 고르지 않았다. 향후 단기 전용 최적화나 고정/온라인 혼합 후보를 연구하려면 새 과거 검증 구간과 아직 보지 않은 미래 평가가 필요하다. 제곱오차 학습과 MAE 평가의 차이, 휴일/학기 변화, 음수 API 관측의 의미, 작은 만차 사건 수가 남은 한계다.

또한 이번 대조군은 기존 검증에서 선택한 고정 α이며, 최근 검증으로 다시 고른 정적 α 대조군은 포함하지 않았다. 따라서120분 개선이 지속 적응 자체의 효과인지, 단순히 기존 .75보다 높은 α가 이 기간에 잘 맞았기 때문인지는 분리하지 못한다. 이 역시 다음의 별도 미래 검증에서 확인할 사항이다.

이번 선택 사전 강도50은 **결과 파일에만** 저장했다. 실제 `online_config.json`은 Phase B의 mode=off·사전 강도200 그대로다. source CSV·고정 α·수집 workflow·예측 workflow·웹사이트·Pages 설정을 변경하지 않았다. shadow 승인 시에도 검토된 선택 설정 반영과 해당 fingerprint의 명시적 bootstrap이 별도로 필요하다.

## 재현

`PROTOCOL.md`, `selection.json`, 후보표, 검증/테스트 예측 CSV와 지표 CSV, 날짜 bootstrap CSV, 원본 범위 감사, 안정성·전체 기준선 대조 JSON, 그림을 함께 제공한다. 수집 CSV는 계속 늘어나므로 위 소스 커밋의 파일을 별도 경로로 추출해 사용한다. 실행 순서·명령은 `REPRODUCE.md`에 있다. 원래 selection은 테스트 전에 고정한 커밋에 보존돼 있으며, 재현 실행의 시간/캐시 압축 해시 등은 달라질 수 있다.
'''
    (out/'REPORT_KO.md').write_text(report,encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


def make_figures(out,predictions,metrics,intervals,daily):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from zoneinfo import ZoneInfo
    figs=out/'figures';figs.mkdir(exist_ok=True)
    def save(name):plt.tight_layout();plt.savefig(figs/name,dpi=160);plt.close()
    fig,axes=plt.subplots(1,2,figsize=(12,4))
    for ax,cohort in zip(axes,['all','clean']):
        for shift_x,model,color in [(-.18,'fixed','#4775b4'),(.18,'online','#e58e36')]:
            values=[float(next(r['mae'] for r in metrics if r['cohort']==cohort and r['model']==model and r['horizon']==str(h) and r['breakdown']=='all' and r['warning_type']=='full')) for h in [15,30,60,120]]
            ax.bar([i+shift_x for i in range(4)],values,.35,label=model,color=color)
        ax.set_xticks(range(4),['15','30','60','120']);ax.set_xlabel('Horizon (minutes)');ax.set_ylabel('MAE (spaces)');ax.set_title(cohort+' matched pairs');ax.legend()
    save('01_mae_comparison.png')
    plt.figure(figsize=(9,4))
    for offset,cohort in [(-.07,'all'),(.07,'clean')]:
        values=[next(r for r in intervals if r['cohort']==cohort and r['horizon']==str(h)) for h in [15,30,60,120]]
        means=[float(r['delta_mae_online_minus_fixed']) for r in values]
        plt.errorbar([i+offset for i in range(4)],means,yerr=[[m-float(r['ci025']) for m,r in zip(means,values)],[float(r['ci975'])-m for m,r in zip(means,values)]],fmt='o',capsize=4,label=cohort)
    plt.axhline(0,color='gray',ls='--');plt.xticks(range(4),['15','30','60','120']);plt.xlabel('Horizon (minutes)');plt.ylabel('Online MAE minus fixed MAE (negative = better)');plt.legend();save('02_paired_day_intervals.png')
    # Illustrative day selected by count of clipping-involved actuals, not errors.
    counts=defaultdict(int)
    for r in predictions:
        if r['h']=='120' and float(r['raw_actual'])<0:counts[r['target'][:10]]+=1
    chosen=max(counts,key=counts.get)
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True)
    tz=ZoneInfo('America/New_York')
    for ax,h in zip(axes,['120','15']):
        rows=sorted([r for r in predictions if r['h']==h and r['target'][:10]==chosen],key=lambda r:r['target'])
        for key,label,color,style in [('actual','actual normalized','black','-'),('raw_actual','raw actual','#999999',':'),('fixed','fixed M3','#4775b4','--'),('online','online M3','#e58e36','-')]:
            xs=[];ys=[];last=None
            for r in rows:
                t=datetime.fromisoformat(r['outcome_timestamp'] if key in ['actual','raw_actual'] else r['target']).astimezone(tz)
                if last and t.timestamp()-last.timestamp()>600:xs.append(t);ys.append(float('nan'))
                xs.append(t);ys.append(float(r[key]));last=t
            ax.plot(xs,ys,label=label,color=color,ls=style,lw=1)
        ax.set_ylabel(h+' min: free spaces');ax.legend(ncol=4,fontsize=8)
    axes[0].set_title('Actual and forecasts: '+chosen+' (America/New_York)');save('03_actual_vs_predicted.png')
    plt.figure(figsize=(11,4))
    for model,index in [('fixed',0),('online',1)]:
        values=sorted((day,part[index]/part[2]) for (day,h),part in daily.items() if h==120)
        plt.plot([d for d,v in values],[v for d,v in values],marker='o',label=model)
    plt.xticks(rotation=40);plt.ylabel('120-minute daily MAE (spaces)');plt.legend();save('04_daily_120_mae.png')
    plt.figure(figsize=(11,4))
    for h in ['15','30','60','120']:
        rows=[r for r in predictions if r['h']==h]
        plt.plot([datetime.fromisoformat(r['origin']).astimezone(tz) for r in rows],[float(r['alpha']) for r in rows],label=h+' min')
    plt.axhline(.75,color='gray',ls='--',lw=.7,label='fixed 120-min alpha');plt.ylabel('Learned alpha (selected setting)');plt.legend(ncol=3);save('05_alpha_trajectories.png')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--csv',required=True);p.add_argument('--source-commit',required=True);p.add_argument('--no-figures',action='store_true');a=p.parse_args()
    build(a.out,a.csv,a.source_commit,not a.no_figures)
