import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from simulation import ROOT,read_data,run_simulation

class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/"config.json").read_text())
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
        self.path=Path(self.tmp.name)/"parking.csv"
        pd.DataFrame(dict(timestamp=["2026-09-29T08:00:00-04:00","2026-09-29T08:04:00-04:00","2026-09-29T08:11:00-04:00","2026-09-29T08:16:00-04:00","2026-09-29T08:21:00-04:00","2026-09-29T09:00:00-04:00"],free_spaces=[600,590,-5,580,600,650],total_spaces=1300)).to_csv(self.path,index=False)
        self.df=read_data(self.path,self.config)
    def tearDown(self): self.tmp.cleanup()
    def test_raw_negative_and_gaps(self):
        self.assertEqual(self.df.free_spaces.iloc[2],-5)
        self.assertFalse(self.df.valid.iloc[2])
        self.assertTrue(self.df.reset.iloc[3]);self.assertTrue(self.df.reset.iloc[5])
        self.assertEqual(self.df.dt_min.iloc[1],4)
        self.assertEqual(self.df.dt_min.iloc[2],7)
    def test_models_conservation_variance_reproducibility(self):
        for model in "ABCD":
            args=(self.df,self.config,{"model":model},40,42)
            s,f,m,snap=run_simulation(*args,snapshot_times=[self.df.timestamp.iloc[4]])
            again=run_simulation(*args)[0]
            pd.testing.assert_frame_equal(s,again)
            self.assertTrue((s.mean_free>=0).all());self.assertTrue((s.q975<=s.capacity).all())
            totals=s.groupby("timestamp").mean_free.sum()
            observed=self.df[self.df.valid].set_index("timestamp").free_spaces
            np.testing.assert_allclose(totals.reindex(observed.index),observed)
            self.assertTrue((s.sd_free>0).any())
            self.assertEqual(m["conservation_checks"],40*5)
            self.assertEqual(m["initializations"],3)
            self.assertTrue((f.incoming_mean>=0).all());self.assertTrue((f.outgoing_mean>=0).all())
    def test_initial_conditions(self):
        a=run_simulation(self.df,self.config,{"model":"D","age":"young"},60,42)[0]
        b=run_simulation(self.df,self.config,{"model":"D","age":"old"},60,42)[0]
        self.assertGreater((a.mean_free-b.mean_free).abs().sum(),0)
        c=run_simulation(self.df,self.config,{"model":"C","initial_preference":"uniform"},60,42)[0]
        d=run_simulation(self.df,self.config,{"model":"C"},60,42)[0]
        self.assertGreater((c.mean_free-d.mean_free).abs().sum(),0)
    def test_clipping_explicit_only(self):
        df=read_data(self.path,self.config,"clip")
        self.assertTrue(df.valid.iloc[2]);self.assertEqual(df.N.iloc[2],1300)
        self.assertEqual(df.free_spaces.iloc[2],-5)
    def test_layouts_and_causal_smoothing(self):
        for caps in self.config["layouts"].values():self.assertEqual(sum(caps),1300);self.assertTrue(all(isinstance(x,int) and x>0 for x in caps))
        before=self.df[["smoothed_N","velocity","acceleration"]].iloc[:2].copy()
        raw=pd.read_csv(self.path);raw.loc[4,"free_spaces"]=10;raw.to_csv(self.path,index=False)
        after=read_data(self.path,self.config)[["smoothed_N","velocity","acceleration"]].iloc[:2]
        pd.testing.assert_frame_equal(before,after)
    def test_duplicate_policy(self):
        raw=pd.read_csv(self.path);pd.concat([raw,raw.iloc[[1]]]).to_csv(self.path,index=False)
        df=read_data(self.path,self.config)
        self.assertEqual(int(df.duplicate.sum()),2)
        self.assertTrue((~df[df.duplicate].valid).all())
    def test_full_empty_and_turnover_bound(self):
        raw=pd.read_csv(self.path).iloc[:2].copy();raw.free_spaces=[0,1300];raw.to_csv(self.path,index=False)
        df=read_data(self.path,self.config);self.assertTrue(df.jump.iloc[1])
        # Force a single interval to exercise maximal exits, independently of reset policy.
        df.loc[1,"reset"]=False
        for model in "ABCD":
            s,f,metrics,_=run_simulation(df,self.config,{"model":model,"scale":"high"},20,19)
            self.assertEqual(s[s.timestamp==df.timestamp.iloc[0]].mean_free.sum(),0)
            self.assertEqual(s[s.timestamp==df.timestamp.iloc[1]].mean_free.sum(),1300)
            self.assertEqual(f.outgoing_mean.iloc[1],1300)
            self.assertEqual(f.incoming_mean.iloc[1],0)

if __name__=="__main__":unittest.main()
