import argparse
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from simulation import ROOT, read_data, run_simulation, audit, intensity

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT.parent / "data/parking.csv")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--quick", action="store_true", help="Small smoke run, not research results")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    out = args.output; out.mkdir(parents=True, exist_ok=True)
    figs = out / "figures"; figs.mkdir(exist_ok=True)
    df = read_data(args.data, config)
    audit_result = audit(df, args.data)
    (out / "audit.json").write_text(json.dumps(audit_result, indent=2))
    df.drop(columns=["time"]).to_csv(out / "observations_audit.csv", index=False)
    days = df.time.dt.strftime("%Y-%m-%d")
    selected = df[days.isin(config["primary_dates"])].copy()
    # Each selected date is a separate chronological experiment, no daily-empty assumption.
    selected.loc[selected.time.dt.date.ne(selected.time.dt.date.shift()), "reset"] = True
    sensitivity = selected[selected.time.dt.strftime("%Y-%m-%d") == config["primary_dates"][0]].copy()
    scenarios = [(m, {"model":m}, config["primary_reps"], selected) for m in "ABCD"]
    variants = {
        "C_layout_B": {"model":"C", "layout":"B_unequal8"},
        "C_layout_C": {"model":"C", "layout":"C_unequal7"},
        "C_weak": {"model":"C", "preference":"weak"},
        "C_strong": {"model":"C", "preference":"strong"},
        "D_short": {"model":"D", "duration":"short"},
        "D_long": {"model":"D", "duration":"long"},
        "C_young": {"model":"C", "age":"young"},
        "C_old": {"model":"C", "age":"old"},
        "A_low": {"model":"A", "scale":"low"},
        "A_high": {"model":"A", "scale":"high"},
    }
    scenarios += [(name, scenario, config["sensitivity_reps"], sensitivity) for name, scenario in variants.items()]
    scenarios.append(("C_uniform_initial", {"model":"C", "preference":"moderate", "initial_preference":"uniform"}, config["sensitivity_reps"], sensitivity))
    # Same observed free count, different histories. Select without simulated outcomes.
    morning = sensitivity[(sensitivity.time.dt.hour>=7)&(sensitivity.time.dt.hour<12)&sensitivity.valid]
    afternoon = sensitivity[(sensitivity.time.dt.hour>=14)&(sensitivity.time.dt.hour<19)&sensitivity.valid]
    pairs = morning[["timestamp","free_spaces"]].merge(afternoon[["timestamp","free_spaces"]], on="free_spaces", suffixes=("_morning","_afternoon"))
    if len(pairs):
        pair = pairs.sort_values("free_spaces").iloc[0].to_dict()
    else:
        # Closest totals if exact match unavailable; report discrepancy explicitly.
        dist = np.abs(morning.free_spaces.to_numpy()[:,None]-afternoon.free_spaces.to_numpy()[None,:])
        i,j = np.unravel_index(dist.argmin(),dist.shape)
        pair = dict(timestamp_morning=morning.timestamp.iloc[i],timestamp_afternoon=afternoon.timestamp.iloc[j],free_spaces=int(morning.free_spaces.iloc[i]),afternoon_free_spaces=int(afternoon.free_spaces.iloc[j]))
    (out/"matched_pair.json").write_text(json.dumps(pair,indent=2))
    summaries, flows, metadata, pair_records = {}, {}, {}, []
    started = time.perf_counter()
    for idx,(name, scenario, reps, data) in enumerate(scenarios):
        if args.quick: reps, data = 20, data.iloc[:12]
        tick = time.perf_counter()
        summary, flow, stats, snapshots = run_simulation(data, config, scenario, reps, config["seed"], [pair["timestamp_morning"],pair["timestamp_afternoon"]])
        summary.to_csv(out/f"{name}_floors.csv",index=False)
        flow.to_csv(out/f"{name}_flows.csv",index=False)
        metadata[name] = dict(parameters=scenario, replications=reps, rows=len(data),valid_rows=int(data.valid.sum()),start=data.timestamp.iloc[0],end=data.timestamp.iloc[-1],seconds=round(time.perf_counter()-tick,3),**stats)
        summaries[name], flows[name] = summary, flow
        if snapshots:
            np.savez_compressed(out/f"{name}_matched_samples.npz", **{k.replace(":","_"):v for k,v in snapshots.items()})
        for period in ["morning", "afternoon"]:
            sub = summary[summary.timestamp==pair[f"timestamp_{period}"]].copy()
            sub["scenario"],sub["period"] = name,period
            pair_records += sub.to_dict("records")
        print(f"{name}: {reps} replicas, {len(data)} observations, {metadata[name]['seconds']} s",flush=True)
    pd.DataFrame(pair_records).to_csv(out/"matched_pair_results.csv",index=False)
    (out/"experiment.json").write_text(json.dumps(dict(configuration=config,scenarios=metadata,total_seconds=time.perf_counter()-started,design="4 primary models x 1000 replicas on two independent observed days; one-factor sensitivity x250 on first day; no daily empty initialization; same seed paired where feasible"),indent=2))
    make_figures(df,selected,summaries,config,figs,pair)
    write_analysis(summaries,metadata,pair,out)

def make_figures(df,selected,results,config,out,pair):
    def save(name):
        plt.tight_layout(); plt.savefig(out/f"{name}.png",dpi=160); plt.close()
    plt.figure(figsize=(12,4));plt.plot(df.time,df.free_spaces,lw=.6);plt.axhline(0,color="red",lw=.8);plt.ylabel("Observed free spaces (raw)");save("01_raw_availability")
    fig,axes=plt.subplots(3,2,figsize=(14,7),sharex="col")
    for j,date in enumerate(config["primary_dates"]):
        sub=selected[selected.time.dt.strftime("%Y-%m-%d")==date]
        for ax,col,label in zip(axes[:,j],["smoothed_N","velocity","acceleration"],["Smoothed occupied","Velocity (vehicles/min)","Acceleration (vehicles/min^2)"]):
            ax.plot(sub.time,sub[col].where(sub.valid & ~sub.reset),lw=.7);ax.set_ylabel(label)
        axes[0,j].set_title(date+"; excluded observations / resets break lines")
    save("02_causal_derivatives")
    day=selected[selected.time.dt.strftime("%Y-%m-%d")==config["primary_dates"][0]]
    plt.figure(figsize=(12,4))
    for m in "ABC": plt.plot(day.time,[intensity(r,m,config) for r in day.itertuples()],label=m)
    plt.ylabel("Assumed simultaneous turnover (vehicles/min)");plt.legend();save("03_intensity")
    s=results["C"].copy();s["time"]=pd.to_datetime(s.timestamp,utc=True).dt.tz_convert("America/New_York")
    s=s[s.time.dt.strftime("%Y-%m-%d")==config["primary_dates"][0]]
    for col,label,name in [("mean_free","Expected free spaces","06_expected_free"),("probability_space","P(at least one space | assumptions)","05_probability")]:
        plt.figure(figsize=(12,5))
        for f,g in s.groupby("floor"):plt.plot(g.time,g[col],label=f"Hypothetical L{f}")
        plt.ylabel(label);plt.legend(ncol=4);save(name)
    plt.figure(figsize=(12,5))
    for f,g in s.groupby("floor"):plt.plot(g.time,g.capacity-g.mean_free,label=f"L{f}")
    plt.ylabel("Expected occupied spaces, hypothetical levels");plt.legend(ncol=4);save("04_floor_occupancy")
    fig,axes=plt.subplots(2,1,figsize=(12,6),sharex=True)
    for ax,f in zip(axes,[1,8]):
        g=s[s.floor==f];ax.plot(g.time,g.mean_free);ax.fill_between(g.time,g.q025,g.q975,alpha=.25);ax.set_ylabel(f"L{f} free; 95% latent interval")
    save("07_monte_carlo_intervals")
    for names,name in [(list("ABCD"),"08_model_comparison"),(["C","C_layout_B","C_layout_C"],"09_layout_sensitivity"),(["C","C_weak","C_strong","D","D_short","D_long"],"10_behavior_sensitivity"),(["C","C_young","C_old","C_uniform_initial"],"11_initial_sensitivity"),(["A","A_low","A_high"],"12_turnover_sensitivity")]:
        plt.figure(figsize=(12,5))
        for n in names:
            g=results[n];g=g[(g.floor==1)&g.timestamp.str.startswith(config["primary_dates"][0])]
            plt.plot(pd.to_datetime(g.timestamp,utc=True).dt.tz_convert("America/New_York"),g.probability_space,label=n)
        plt.ylabel("Hypothetical L1: P(at least one free space)");plt.legend(ncol=3);save(name)
    fig,axes=plt.subplots(1,2,figsize=(12,4))
    for ax,period in zip(axes,["morning","afternoon"]):
        for n in "ABCD":
            g=results[n];g=g[g.timestamp==pair[f"timestamp_{period}"]]
            ax.plot(g.floor,g.mean_free,marker="o",label=n)
        ax.set_title(period+": "+pair[f"timestamp_{period}"]);ax.set_xlabel("Hypothetical level");ax.set_ylabel("Expected free spaces");ax.legend()
    save("13_matched_availability_histories")

def write_analysis(results,metadata,pair,out):
    base=results["C"]
    comparison=[]
    for n,s in results.items():
        l1=s[s.floor==1]
        comparable=base.merge(s,on=["timestamp","floor"],suffixes=("_C","_scenario"))
        comparison.append(dict(scenario=n,replications=metadata[n]["replications"],mean_L1_probability=float(l1.probability_space.mean()),mean_L1_free=float(l1.mean_free.mean()),mean_abs_free_difference_from_C=float((comparable.mean_free_C-comparable.mean_free_scenario).abs().mean()),max_abs_probability_difference_from_C=float((comparable.probability_space_C-comparable.probability_space_scenario).abs().max()),early_departure_fraction=metadata[n]["early_departures"]/max(1,metadata[n]["departures"]),departures_per_replica=metadata[n]["departures"]/metadata[n]["replications"]))
    pd.DataFrame(comparison).to_csv(out/"scenario_comparison.csv",index=False)
    # JSON is a research artifact, with an explicit interpretation boundary.
    payload=dict(type="hypothetical_floor_simulation",warning="Not empirically calibrated. No floor ground truth. Not production predictions.",matched_pair=pair,comparison=comparison)
    (out/"summary.json").write_text(json.dumps(payload,indent=2))

if __name__=="__main__": main()
