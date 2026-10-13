import test from 'node:test';
import assert from 'node:assert/strict';
import {serviceState} from '../../../site/state.js';
const now=Date.parse('2026-10-13T16:00:00Z');
function payload(){return {schema_version:3,status:'ok',forecast_origin:new Date(now).toISOString(),generated_at:new Date(now).toISOString(),freshness_seconds:1200,future_clock_tolerance_seconds:120,current:{timestamp:new Date(now).toISOString(),free_spaces:-5},predictions:[15,30,60,120].map(h=>({horizon_minutes:h,target_time:new Date(now+h*60000).toISOString(),free_spaces:-10,warning_cutoff:25}))};}
test('fresh negative forecasts are shown',()=>assert.equal(serviceState(payload(),now).usable,true));
test('forecasts expire while page remains open',()=>assert.equal(serviceState(payload(),now+1201000).reason,'stale_observation'));
test('network error hides cached forecasts',()=>assert.equal(serviceState(payload(),now,false).usable,false));
test('future sensor timestamp hides forecasts',()=>assert.equal(serviceState(payload(),now-121000).reason,'future_timestamp'));
test('server gap status is respected',()=>{let p=payload();p.status='unavailable';p.reason='insufficient_recent_history';assert.equal(serviceState(p,now).usable,false);});
test('missing horizon and numeric strings rejected',()=>{let p=payload();p.predictions.pop();assert.equal(serviceState(p,now).usable,false);p=payload();p.predictions[0].free_spaces='20';assert.equal(serviceState(p,now).usable,false);});
test('wrong target and missing freshness rejected',()=>{let p=payload();p.predictions[0].target_time=p.forecast_origin;assert.equal(serviceState(p,now).usable,false);p=payload();delete p.freshness_seconds;assert.equal(serviceState(p,now).usable,false);});
