"""Read-only export from an authenticated committed online checkpoint.

Production uses stored learned forecasts for all operational horizons. The
historical shadow mode retains fixed-alpha operational predictions.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from calendar_features import load_calendar, classify
from export_prediction import instant, elapsed, shift, recent_history_ok
from observations import normalize_free, canonical_csv_bytes
from online_model import OnlineModel, from_stamp
from online_state import load_state, StateError


def cached_payload(csv_path, state_path, cfg, options, now=None, revision=None):
    calendar = load_calendar()
    state, recovered = load_state(state_path, cfg, options, calendar)
    if recovered:
        raise StateError('Recovered state must be caught up and saved before export')
    checkpoint = state['checkpoint']
    if state['diagnostics']['quarantined_closed_day']:
        raise StateError('Unresolved historical corrections require explicit bootstrap')
    content = Path(csv_path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    canonical_content = canonical_csv_bytes(content)
    if checkpoint['offset'] != len(canonical_content) or checkpoint['prefix_sha256'] != hashlib.sha256(canonical_content).hexdigest():
        raise StateError('Checkpoint does not cover this exact CSV snapshot')
    model = OnlineModel(cfg, options, calendar, state)
    production=options['mode']=='production'
    receipt_path=Path(str(state_path)+'.remote')
    receipt=json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
    if state['last_event'] is None:
        raise StateError('Empty checkpoint')
    origin = from_stamp(state['last_event'], model.tz)
    now = (now or datetime.now(timezone.utc)).astimezone(model.tz)
    raw = next(r for r in state['recent'] if r['timestamp'] == state['last_event'])
    quality = dict(state['quality'])
    quality.update(clipped_rows=quality['clipped_to_zero']+quality['clipped_to_capacity'],
                   rejected_rows=quality['invalid_rows'], duplicate_rows=state['diagnostics']['duplicates'])
    data = dict(schema_version=3, parking='T Deck', timezone=cfg['timezone'], generated_at=now.isoformat(),
                freshness_seconds=cfg['freshness_seconds'], future_clock_tolerance_seconds=cfg['future_clock_tolerance_seconds'],
                status='unavailable', reason=None, current=dict(timestamp=origin.isoformat(), free_spaces=raw['free'],
                total_spaces=raw['capacity'], model_free_spaces=normalize_free(raw['free'])),
                history=[dict(timestamp=from_stamp(r['timestamp'], model.tz).isoformat(), free_spaces=r['free']) for r in state['recent']],
                predictions=[], shadow_predictions=[], online_shadow_predictions=[],
                model='M3-online' if production else 'M3', parameters_available_from=cfg['parameters_available_from'],
                baseline_history_policy=cfg['baseline_history_policy'], source_repository=cfg['source_repository'],
                source_revision=revision, source_sha256=digest, quality=quality,
                forecast_origin=origin.isoformat(), valid_until=shift(origin,cfg['freshness_seconds']).isoformat(),
                online=dict(mode=options['mode'], operational_model='online_alpha_M3' if production else 'fixed_alpha_M3', state_version=state['schema_version'],
                            model_version=state['model_version'], trained_through=state['last_successful_update'],
                            parameter_status=options['parameter_status'],configuration_fingerprint=state['fingerprint'],
                            state_revision=receipt.get('head'),state_checksum=json.loads(Path(state_path).read_text(encoding='utf-8'))['checksum'],
                            training_counts={h:o['pairs'] for h,o in state['optimizers'].items()}))
    data['calendar'] = classify(origin.date(), calendar)
    data['calendar']['prediction_policy'] = 'shadow_only'
    age = elapsed(now, origin)
    reason = None
    if age < -cfg['future_clock_tolerance_seconds']: reason = 'future_timestamp'
    elif age > cfg['freshness_seconds']: reason = 'stale_observation'
    elif state['latest_invalid'] and state['latest_invalid'] > state['last_event']: reason = 'invalid_latest_observation'
    elif instant(origin) < instant(datetime.fromisoformat(cfg['parameters_available_from'])): reason = 'parameters_not_available'
    elif sum(state['baseline']['date_counts']) < cfg['minimum_history_days']: reason = 'insufficient_calendar_history'
    elif not recent_history_ok(model._recent(), origin, cfg): reason = 'insufficient_recent_history'
    if reason:
        data.update(status='stale' if reason == 'stale_observation' else 'unavailable', reason=reason)
        return data
    b_now, _ = model.baseline(origin)
    shadow_now, now_policy = model.baseline(origin, True)
    x = normalize_free(raw['free'])-b_now
    learned={p['horizon']:p for p in state['last_forecasts'] if p['origin']==state['last_event']}
    if production and set(learned)!={h['minutes'] for h in cfg['horizons']}:
        raise StateError('Missing operational online forecasts; fixed model is not substituted')
    for h in cfg['horizons']:
        target = shift(origin,h['minutes']*60)
        baseline, _ = model.baseline(target)
        value = learned[h['minutes']]['prediction'] if production else normalize_free(baseline+h['alpha']*x)
        data['predictions'].append(dict(horizon_minutes=h['minutes'], target_time=target.isoformat(),free_spaces=value,
                                       full=value<=0, warning=value<=h['warning_cutoff'],warning_cutoff=h['warning_cutoff']))
        if production:data['predictions'][-1]['alpha']=learned[h['minutes']]['alpha']
        shadow_target, policy = model.baseline(target, True)
        candidate = normalize_free(shadow_target+h['alpha']*(normalize_free(raw['free'])-shadow_now))
        data['shadow_predictions'].append(dict(horizon_minutes=h['minutes'],target_time=target.isoformat(),free_spaces=candidate,
                                              now_calendar_policy=now_policy,target_calendar_policy=policy,
                                              note='Calendar research candidate only; not displayed as operational forecast'))
    for p in ([] if production else state['last_forecasts']):
        if p['origin'] != state['last_event']:
            continue
        data['online_shadow_predictions'].append(dict(horizon_minutes=p['horizon'],target_time=from_stamp(p['target'],model.tz).isoformat(),
                free_spaces=p['prediction'],alpha=p['alpha'],note='Unvalidated online candidate; origin features frozen; not an operational forecast'))
    data.update(status='ok',reason=None)
    return data
