"""Write a separate date audit; never append columns to the collector CSV."""
import argparse
import csv
from pathlib import Path
from zoneinfo import ZoneInfo
from calendar_features import load_calendar,classify
from export_prediction import read_observations

def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',default='data/parking.csv');p.add_argument('--out',default='calendar-audit.csv');a=p.parse_args()
    cal=load_calendar();rows,_=read_observations(a.csv,ZoneInfo('America/New_York'));dates=sorted({x['time'].date() for x in rows})
    output=[]
    for day in dates:
        f=classify(day,cal);f['observations']=sum(x['time'].date()==day for x in rows)
        f['sources']='; '.join(f['sources']);f['exception_names']='; '.join(f['exception_names']);output.append(f)
    with Path(a.out).open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=output[0].keys());writer.writeheader();writer.writerows(output)
if __name__=='__main__':main()
