"""Transactional incremental update. Off by default, no implicit bootstrap."""
import argparse
import json
from pathlib import Path
import time
from datetime import datetime, timezone

from bootstrap_online import ROOT
from calendar_features import load_calendar
from online_model import OnlineModel
from online_ingest import ingest
from online_state import state_lock, load_state, save_state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', default='data/parking.csv')
    p.add_argument('--state', required=True)
    p.add_argument('--config', default=str(ROOT/'config.json'))
    p.add_argument('--online-config', default=str(ROOT/'online_config.json'))
    p.add_argument('--offline', action='store_true', help='Explicit local testing/replay; never used by production workflow')
    args = p.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    options = json.loads(Path(args.online_config).read_text(encoding='utf-8'))
    if options['mode'] != 'shadow' and not args.offline:
        print('Online learning is off; no state read or written')
        return
    start = time.perf_counter()
    calendar = load_calendar()
    with state_lock(args.state):
        state, recovered = load_state(args.state, cfg, options, calendar)
        model = OnlineModel(cfg, options, calendar, state)
        result = ingest(model, args.csv, None if args.offline else datetime.now(timezone.utc))
        if result['new_bytes'] or recovered:
            save_state(args.state, model.state, cfg, options, calendar)
    print(json.dumps(dict(ingestion=result, recovered=recovered, seconds=time.perf_counter()-start,
                         bytes=Path(args.state).stat().st_size), indent=2))


if __name__ == '__main__':
    main()
