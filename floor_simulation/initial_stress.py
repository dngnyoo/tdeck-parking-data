"""Informative initial-state expansion: begin with 800 observed vehicles."""
import json
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from simulation import ROOT,read_data,run_simulation,audit

def main():
    config=json.loads((ROOT/"config.json").read_text())
    path=ROOT.parent/"data/parking.csv"
    df=read_data(path,config)
    hours=df.time.dt.hour+df.time.dt.minute/60
    df=df[(df.time.dt.strftime("%Y-%m-%d")=="2026-09-29")&(hours>=9.5)&(hours<16)].copy()
    output=ROOT/"results/initial_stress";output.mkdir(parents=True,exist_ok=True)
    results,metadata={},{}
    scenarios={"mixed":{"model":"D"},"young":{"model":"D","age":"young"},"old":{"model":"D","age":"old"},"uniform":{"model":"D","initial_preference":"uniform"}}
    for name,scenario in scenarios.items():
        tick=time.perf_counter();s,f,m,_=run_simulation(df,config,scenario,1000,config["seed"])
        s.to_csv(output/f"{name}_floors.csv",index=False);f.to_csv(output/f"{name}_flows.csv",index=False)
        results[name]=s
        metadata[name]=dict(scenario=scenario,replications=1000,seed=config["seed"],seconds=time.perf_counter()-tick,**m)
        print(name,round(metadata[name]["seconds"],2),flush=True)
    rows=[]
    for name,s in results.items():
        paired=results["mixed"].merge(s,on=["timestamp","floor"],suffixes=("_base","_test"))
        # Separate initial construction from later dynamics.
        for label,g in [("all",paired),("after_30min",paired[paired.timestamp>=df.timestamp.iloc[6]])]:
            rows.append(dict(scenario=name,period=label,mean_abs_free_difference=float((g.mean_free_base-g.mean_free_test).abs().mean()),max_probability_difference_pp=float((g.probability_space_base-g.probability_space_test).abs().max()*100)))
    pd.DataFrame(rows).to_csv(output/"comparison.csv",index=False)
    (output/"metadata.json").write_text(json.dumps(dict(audit=audit(df,path),config=config,scenarios=metadata,comparison=rows),indent=2))
    plt.figure(figsize=(12,5))
    for name,s in results.items():
        g=s[s.floor==1];plt.plot(pd.to_datetime(g.timestamp,utc=True).dt.tz_convert("America/New_York"),g.probability_space,label=name)
    plt.ylabel("L1: assumption-conditional P(space), busy initialization");plt.legend();plt.tight_layout();plt.savefig(ROOT/"results/figures/14_busy_initial_conditions.png",dpi=160);plt.close()

if __name__=="__main__":main()
