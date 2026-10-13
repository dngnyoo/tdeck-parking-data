from datetime import datetime
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from calendar_features import load_calendar,classify,baseline_history

class CalendarTests(unittest.TestCase):
    def setUp(self):self.calendar=load_calendar()
    def test_gsu_labor_day(self):
        f=classify('2026-09-07',self.calendar)
        self.assertTrue(f['gsu_no_class_confirmed']);self.assertTrue(f['university_closed_confirmed']);self.assertTrue(f['federal_holiday'])
    def test_federal_is_not_gsu_closure(self):
        f=classify('2026-10-12',self.calendar)
        self.assertTrue(f['federal_holiday']);self.assertFalse(f['gsu_no_class_confirmed']);self.assertEqual(f['day_type'],'term_weekday')
        self.assertEqual(f['schedule_basis'],'full_semester');self.assertEqual(f['label'],'풀 학기 평일')
    def test_break_and_closed_are_distinct(self):
        f=classify('2026-11-23',self.calendar)
        self.assertEqual(f['day_type'],'academic_break');self.assertFalse(f['university_closed_confirmed'])
        self.assertEqual(classify('2026-11-26',self.calendar)['day_type'],'university_closed')
    def test_unknown_and_expiration(self):
        f=classify('2027-01-05',self.calendar)
        self.assertFalse(f['calendar_known']);self.assertIsNone(f['gsu_no_class_confirmed']);self.assertIsNone(f['federal_holiday'])
    def test_holiday_support_fallback(self):
        rows=[{'time':datetime.fromisoformat('2026-09-07T12:00:00-04:00'),'free':1000}]
        chosen,policy=baseline_history(rows,datetime.fromisoformat('2026-11-23T12:00:00-05:00'),self.calendar)
        self.assertEqual(chosen,rows);self.assertIn('insufficient',policy)
    def test_regular_weekday_excludes_only_gsu_exception(self):
        rows=[{'time':datetime.fromisoformat(t),'free':100} for t in ['2026-09-07T12:00:00-04:00','2026-10-12T12:00:00-04:00']]
        chosen,_=baseline_history(rows,datetime.fromisoformat('2026-10-19T12:00:00-04:00'),self.calendar)
        self.assertEqual(len(chosen),1);self.assertEqual(chosen[0]['time'].date().isoformat(),'2026-10-12')

if __name__=='__main__':unittest.main()
