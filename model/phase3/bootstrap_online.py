"""Explicit one-time chronological initialization; never called by routine jobs."""
import argparse
import json
from pathlib import Path
import time

from calendar_features import load_calendar
from online_model import OnlineModel
from online_ingest import ingest
from online_state import state_lock, save_state

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', default='data/parking.csv')
    p.add_argument('--state', required=True, help='Local output checkpoint; not automatically published')
    p.add_argument('--config', default=str(ROOT/'config.json'))
    p.add_argument('--online-config', default=str(ROOT/'online_config.json'))
    p.add_argument('--replace', action='store_true', help='Explicitly replace an existing checkpoint')
    args = p.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    options = json.loads(Path(args.online_config).read_text(encoding='utf-8'))
    calendar = load_calendar()
    start = time.perf_counter()
    with state_lock(args.state):
        if Path(args.state).exists() and not args.replace:
            p.error('State exists; choose another path or explicitly use --replace')
        model = OnlineModel(cfg, options, calendar)
        result = ingest(model, args.csv)
        save_state(args.state, model.state, cfg, options, calendar)
    print(json.dumps(dict(offline_bootstrap=True, seconds=time.perf_counter()-start,
                         bytes=Path(args.state).stat().st_size, ingestion=result,
                         quality=model.state['quality'], optimizers=model.state['optimizers']), indent=2))


if __name__ == '__main__':
    main()
