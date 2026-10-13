export const horizons = [15, 30, 60, 120];
export function serviceState(data, now = Date.now(), connected = true) {
  if (!data || data.schema_version !== 3 || !data.current) return {usable:false, reason:'unavailable'};
  if (!Number.isFinite(data.freshness_seconds) || data.freshness_seconds<=0 || data.freshness_seconds>1200 || !Number.isFinite(data.future_clock_tolerance_seconds) || data.future_clock_tolerance_seconds<0 || data.future_clock_tolerance_seconds>120) return {usable:false,reason:'invalid'};
  const origin = Date.parse(data.forecast_origin);
  const generated = Date.parse(data.generated_at);
  const age = (now-origin)/1000;
  if (!Number.isFinite(origin) || !Number.isFinite(generated) || !Number.isFinite(data.current.free_spaces) || Date.parse(data.current.timestamp)!==origin) return {usable:false,reason:'invalid'};
  if (age < -data.future_clock_tolerance_seconds || generated > now+120000) return {usable:false,reason:'future_timestamp'};
  if (!connected) return {usable:false,reason:'connection_error'};
  if (age > data.freshness_seconds) return {usable:false,reason:'stale_observation'};
  if (data.status !== 'ok') return {usable:false,reason:data.reason || 'unavailable'};
  if (!Array.isArray(data.predictions) || data.predictions.length !== 4) return {usable:false,reason:'invalid'};
  for (const h of horizons) {
    const rows=data.predictions.filter(p=>p.horizon_minutes===h);
    if(rows.length!==1 || !Number.isFinite(rows[0].free_spaces) || !Number.isFinite(rows[0].warning_cutoff) || Date.parse(rows[0].target_time)!==origin+h*60000) return {usable:false,reason:'invalid'};
  }
  return {usable:true,reason:null};
}
