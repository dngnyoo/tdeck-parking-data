"""Reviewed calendar metadata and a non-production baseline candidate."""
from datetime import date,timedelta
import json
from pathlib import Path

PATH=Path(__file__).with_name('academic_calendar.json')
def load_calendar(path=PATH):return json.loads(Path(path).read_text(encoding='utf-8'))

def classify(day,calendar):
    if not isinstance(day,date):day=date.fromisoformat(day)
    key=day.isoformat();exceptions=[x for x in calendar['exceptions'] if x['start']<=key<=x['end']]
    term=calendar['term'];in_term=term['start']<=key<=term['classes_end'];known=in_term or bool(exceptions)
    closed=any(x['university_closed'] is True for x in exceptions)
    no_class=bool(exceptions) if known else None
    kind=('university_closed' if closed else 'academic_break') if exceptions else ('weekend' if day.weekday()>=5 else 'term_weekday') if in_term else 'unknown'
    labels={'university_closed':'GSU 휴무','academic_break':'GSU 수업 휴식','weekend':'학기 중 주말','term_weekday':'학기 중 평일','unknown':'학사 일정 미확인'}
    federal_name=calendar['federal_observed'].get(key)
    federal=bool(federal_name) if day.year in calendar['federal_years_complete'] or federal_name else None
    exception_days={date.fromisoformat(x['start'])+timedelta(days=i) for x in calendar['exceptions'] for i in range((date.fromisoformat(x['end'])-date.fromisoformat(x['start'])).days+1)}
    return dict(date=key,day_type=kind,label=labels[kind],calendar_known=known,gsu_no_class_confirmed=no_class,
                university_closed_confirmed=closed if known else None,in_full_semester=in_term,
                federal_holiday=federal,federal_holiday_name=federal_name,
                adjacent_to_gsu_exception=(day-timedelta(days=1) in exception_days or day+timedelta(days=1) in exception_days),
                exception_names=[x['label'] for x in exceptions],sources=list(dict.fromkeys(x['source'] for x in exceptions)),
                calendar_version=calendar['version'],scope=calendar['scope'])

def baseline_history(history,target,calendar):
    """Only past readings and known date rules; no parking targets are inspected."""
    target_type=classify(target.date(),calendar)
    if not target_type['calendar_known'] or target_type['day_type']=='weekend':return history,'unchanged'
    labels={day:classify(day,calendar)['gsu_no_class_confirmed'] for day in {x['time'].date() for x in history}}
    if target_type['gsu_no_class_confirmed']:
        matching=[x for x in history if labels[x['time'].date()] is True]
        if len({x['time'].date() for x in matching})<calendar['minimum_exception_dates']:return history,'fallback_insufficient_exception_dates'
        return matching,'exception_history'
    matching=[x for x in history if labels[x['time'].date()] is not True]
    return (matching,'exclude_gsu_exceptions') if matching else (history,'fallback_empty')
