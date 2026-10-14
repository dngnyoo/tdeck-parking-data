"""Standalone configurable replay, including complete historical coverage."""
import argparse
import json
import time
from pathlib import Path
from simulation import ROOT,read_data,run_simulation,audit

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data",type=Path,default=ROOT.parent/"data/parking.csv")
    p.add_argument("--config",type=Path,default=ROOT/"config.json")
    p.add_argument("--output",type=Path,default=ROOT/"results/full_history")
    p.add_argument("--model",choices=list("ABCD"),default="C")
    p.add_argument("--layout",default="A_equal8")
    p.add_argument("--preference",choices=["weak","moderate","strong"],default="moderate")
    p.add_argument("--duration",choices=["standard","short","long"],default="standard")
    p.add_argument("--age",choices=["young","mixed","old"],default="mixed")
    p.add_argument("--scale",choices=["low","medium","high"],default="medium")
    p.add_argument("--policy",choices=["exclude","clip"],default="exclude")
    p.add_argument("--replications",type=int,default=100)
    p.add_argument("--seed",type=int,default=20261013)
    p.add_argument("--date",help="Optional local YYYY-MM-DD; initialize at first observation")
    p.add_argument("--start-hour",type=float,default=0,help="With --date, start at or after this local hour")
    p.add_argument("--end-hour",type=float,default=24,help="With --date, end before this local hour")
    p.add_argument("--initial-preference",choices=["same","uniform"],default="same")
    args=p.parse_args()
    if args.replications<1:p.error("replications must be positive")
    config=json.loads(args.config.read_text())
    df=read_data(args.data,config,args.policy)
    if args.date:
        hours=df.time.dt.hour+df.time.dt.minute/60
        df=df[(df.time.dt.strftime("%Y-%m-%d")==args.date)&(hours>=args.start_hour)&(hours<args.end_hour)].copy()
    if not len(df):p.error("no observations selected")
    scenario={key:getattr(args,key) for key in ["model","layout","preference","duration","age","scale"]}
    if args.initial_preference=="uniform":scenario["initial_preference"]="uniform"
    start=time.perf_counter()
    s,f,metrics,_=run_simulation(df,config,scenario,args.replications,args.seed)
    args.output.mkdir(parents=True,exist_ok=True)
    s.to_csv(args.output/"floors.csv",index=False)
    f.to_csv(args.output/"flows.csv",index=False)
    metadata=dict(scenario=scenario,config=config,replications=args.replications,seed=args.seed,policy=args.policy,audit=audit(df,args.data),seconds=time.perf_counter()-start,**metrics)
    (args.output/"metadata.json").write_text(json.dumps(metadata,indent=2))
    print(json.dumps(metadata,indent=2),flush=True)

if __name__=="__main__":main()
