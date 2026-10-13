import {serviceState,horizons} from './state.js';
const $=id=>document.getElementById(id);
let data=null,connected=true,busy=false;
const time = value => new Intl.DateTimeFormat('ko-KR',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
const stamp = value => new Intl.DateTimeFormat('ko-KR',{timeZone:'America/New_York',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
const spaces = value => Math.round(value).toLocaleString('ko-KR');
const messages={stale_observation:'마지막 관측이 20분을 넘었습니다. 새 자료가 도착할 때까지 예측을 숨깁니다.',insufficient_recent_history:'최근 관측에 공백이 있어 예측을 표시하지 않습니다.',insufficient_calendar_history:'예측에 필요한 과거 자료를 모으고 있습니다.',future_timestamp:'관측 시각이나 기기 시계가 맞지 않아 예측을 표시하지 않습니다.',connection_error:'새 자료를 확인할 수 없습니다. 연결이 복구될 때까지 예측을 숨깁니다.',capacity_anomaly:'주차장 수용량 기록을 확인해야 합니다. 예측을 잠시 숨깁니다.',parameters_not_available:'이 관측 시각에는 검증된 예측 설정을 적용할 수 없습니다.',invalid:'자료 형식을 확인할 수 없어 예측을 표시하지 않습니다.',unavailable:'주차장 자료를 불러올 수 없습니다. 잠시 후 다시 확인해주세요.'};
function cell(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
function render(){
  const state=serviceState(data,Date.now(),connected);
  $('notice').hidden=state.usable || (!data && busy);
  $('notice').textContent=messages[state.reason] || messages.unavailable;
  $('connection').className=`connection ${state.usable?'fresh':'stale'}`;
  $('connection').replaceChildren(cell('i',''),cell('span',state.usable?'최근 자료 연결됨':(!data&&busy?'자료 확인 중':'업데이트 대기')));
  const observed=data?.current;
  $('current-value').textContent=observed && Number.isFinite(observed.free_spaces)?spaces(observed.free_spaces):'—';
  $('observed-badge').textContent=state.usable?'관측값':'마지막 관측';
  $('capacity').textContent=`총 ${spaces(observed?.total_spaces || 1300)}면`;
  $('observed-time').textContent=observed?.timestamp && Number.isFinite(Date.parse(observed.timestamp))?`${stamp(observed.timestamp)} 관측`:'관측 없음';
  $('current-description').textContent=!observed?'주차장 자료를 기다리고 있습니다.':observed.free_spaces<=0?'잔여 공간 없음 또는 초과 집계':state.usable?'주차장 자료에 기록된 현재값입니다.':'이 값은 과거 관측입니다. 현재 상태가 아닙니다.';
  $('forecast-cards').replaceChildren();
  for(const h of horizons){
    const prediction=state.usable?data.predictions.find(p=>p.horizon_minutes===h):null;
    const card=cell('article','');card.className=`forecast-card ${prediction?'':'unavailable'}`;
    card.append(cell('div',`${h}분 뒤`));card.firstChild.className='forecast-horizon';
    const t=cell('div',prediction?`${time(prediction.target_time)} 예상`:'새 자료 대기');t.className='forecast-time';card.append(t);
    const number=cell('div',prediction?spaces(prediction.free_spaces):'—');number.className='forecast-number';if(prediction)number.append(cell('small','면'));card.append(number);
    const status=prediction?(prediction.free_spaces<=0?'잔여 공간 없음 예상':prediction.free_spaces<=prediction.warning_cutoff?'혼잡 가능':'여유 예상'):'예측 표시 중지';
    const label=cell('div',status);label.className=`forecast-status ${prediction?.free_spaces<=0?'full':prediction?.free_spaces<=prediction?.warning_cutoff?'warning':''}`;card.append(label);$('forecast-cards').append(card);
  }
  drawChart(state.usable);
}
function drawChart(usable){
  const history=(Array.isArray(data?.history)?data.history:[]).filter(p=>Number.isFinite(p.free_spaces)&&Number.isFinite(Date.parse(p.timestamp)));
  const forecasts=usable?data.predictions:[];
  $('forecast-legend').hidden=!usable;
  const body=$('chart-data');body.replaceChildren();
  for(const p of history){const tr=document.createElement('tr');tr.append(cell('td','관측'),cell('td',stamp(p.timestamp)),cell('td',`${p.free_spaces}면`));body.append(tr);}
  for(const p of forecasts){const tr=document.createElement('tr');tr.append(cell('td',`${p.horizon_minutes}분 예측`),cell('td',stamp(p.target_time)),cell('td',`${p.free_spaces.toFixed(2)}면`));body.append(tr);}
  if(!history.length){$('chart').replaceChildren(cell('p','표시할 관측이 없습니다.'));$('chart').firstChild.className='chart-empty';return;}
  const points=history.map(p=>({x:Date.parse(p.timestamp),y:p.free_spaces}));
  const future=forecasts.map(p=>({x:Date.parse(p.target_time),y:p.free_spaces}));
  const all=points.concat(future);const start=Math.min(...all.map(p=>p.x)),end=Math.max(...all.map(p=>p.x),start+60000);
  const ymin=Math.min(0,...all.map(p=>p.y));const ymax=Math.max(200,...all.map(p=>p.y));
  const lo=Math.floor(ymin/100)*100,hi=Math.ceil((ymax+30)/100)*100;
  const W=Math.max(320,$('chart').clientWidth),H=240,L=44,R=18,T=12,B=35;
  const x=v=>L+(v-start)/(end-start)*(W-L-R);const y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('role','img');svg.setAttribute('aria-label','최근 관측과 미래 예측. 상세 수치는 그래프 수치 보기에 있습니다.');
  const make=(name,attrs,text)=>{const el=document.createElementNS(ns,name);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;svg.append(el);return el;};
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;make('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:'#e6eded'});make('text',{x:L-10,y:y(v)+4,'text-anchor':'end','font-size':11,fill:'#6f8186'},Math.round(v));}
  make('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:'#ce9a8b','stroke-dasharray':'4 4'});
  // Draw separate segments across missing intervals; do not invent observations.
  let segments=[],segment=[];for(const p of points){if(segment.length&&p.x-segment.at(-1).x>600000){segments.push(segment);segment=[];}segment.push(p);}segments.push(segment);
  for(const s of segments)make('polyline',{points:s.map(p=>`${x(p.x)},${y(p.y)}`).join(' '),fill:'none',stroke:'#1d3b44','stroke-width':2.3});
  if(future.length){const now=points.at(-1);make('polyline',{points:[now,...future].map(p=>`${x(p.x)},${y(p.y)}`).join(' '),fill:'none',stroke:'#167a68','stroke-width':2.3,'stroke-dasharray':'6 5'});for(const p of future){const dot=make('circle',{cx:x(p.x),cy:y(p.y),r:4,fill:'#167a68'});const title=document.createElementNS(ns,'title');title.textContent=`${time(p.x)} 예측 ${p.y.toFixed(2)}면`;dot.append(title);}}
  for(let i=0;i<6;i++){const v=start+(end-start)*i/5;make('text',{x:x(v),y:H-10,'text-anchor':'middle','font-size':11,fill:'#6f8186'},time(v));}
  $('chart').replaceChildren(svg);$('chart-caption').textContent=usable?'실선은 관측, 점선은 같은 현재 관측에서 계산한 네 미래 예측 시점입니다.':'과거 관측만 표시합니다. 새 자료가 도착하면 미래 예측이 다시 나타납니다.';
}
async function refresh(){
  if(busy)return;busy=true;$('refresh').disabled=true;render();
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),12000);
  try{const response=await fetch(`./data/prediction.json?at=${Date.now()}`,{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error('HTTP');const next=await response.json();if(next.schema_version!==3)throw Error('Schema');data=next;connected=true;}catch{connected=false;}finally{clearTimeout(timeout);busy=false;$('refresh').disabled=false;render();}
}
$('refresh').addEventListener('click',refresh);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});window.addEventListener('resize',()=>drawChart(serviceState(data,Date.now(),connected).usable));setInterval(refresh,60000);setInterval(render,15000);refresh();
