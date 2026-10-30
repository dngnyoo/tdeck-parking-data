"""Integration core; production Cron/model/Telegram adapters are NOT connected yet."""
from datetime import datetime, timezone
from copy import deepcopy
from contextlib import closing
import hashlib
import sqlite3

from .freshness import check


class Gate:
    def __init__(self, database, config=None):
        self.database = database
        self.config = config
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, state TEXT NOT NULL)')

    def run(self, payload, *, event_scope, eligible, model, send, clock=None):
        """Only trusted adapter callbacks; model output cannot change destination/permission.

        event_scope must identify the existing policy's event/window/recipient; it must
        NOT be random per run. Reservation survives ambiguous delivery, blocking retries.
        No automatic recovery of a pending event: inspect provider receipt first.
        """
        clock = clock or (lambda: datetime.now(timezone.utc))
        payload = deepcopy(payload)
        decision = check(payload, now=clock(), config=self.config)
        if not decision['allow_model']:
            return dict(decision, outcome='blocked')
        if not eligible(deepcopy(payload)):
            return dict(decision, outcome='condition_not_met')
        decision = check(payload, now=clock(), config=self.config)
        if not decision['allow_model']:
            return dict(decision, outcome='blocked')
        if not isinstance(event_scope, str) or not event_scope.strip():
            raise ValueError('An existing notification policy event scope is required')
        event_id = hashlib.sha256((event_scope + ':' + decision['observation_id']).encode()).hexdigest()
        with closing(sqlite3.connect(self.database)) as db, db:
            cursor = db.execute('INSERT OR IGNORE INTO events VALUES (?, ?)', (event_id, 'pending'))
            if not cursor.rowcount:
                return dict(decision, outcome='duplicate_or_pending')
        try:
            # The trusted source snapshot is not handed to callbacks for mutation.
            text = model(dict(decision))
            final = check(payload, now=clock(), config=self.config)
            if not final['allow_notification']:
                state = 'expired_before_send'
            elif not isinstance(text, str) or not text.strip():
                state = 'invalid_model_output'
            else:
                # Sender must have a fixed destination and return provider receipt ID.
                receipt = send(text)
                state = 'handed_off' if isinstance(receipt, str) and receipt.strip() else 'delivery_unknown'
        except Exception:
            # Never retry automatically after an ambiguous model/send failure.
            state = 'delivery_unknown'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE events SET state=? WHERE id=?', (state, event_id))
        return dict(final if 'final' in locals() else decision, outcome=state)
