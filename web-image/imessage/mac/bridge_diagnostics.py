"""Read-only bounded metadata; no message IDs/body, filename, account, URL or secret."""
import argparse
import contextlib
import json
from pathlib import Path
import sqlite3
import time


def snapshot(home):
    home=Path(home);path=home/'state.db'
    with contextlib.closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
        control=dict(db.execute('SELECT enabled,pending_off,phase,images_enabled,control_acked_at FROM control WHERE id=1').fetchone())
        counts={r['status']:r['n'] for r in db.execute('SELECT status,count(*) AS n FROM ledger GROUP BY status') if r['status'] in ['intent','unknown','submitted','cancelled']}
        total=db.execute('SELECT count(*) FROM ledger').fetchone()[0]
        pages=db.execute('PRAGMA page_count').fetchone()[0];free=db.execute('PRAGMA freelist_count').fetchone()[0]
    result={'control':{k:control[k] for k in ['enabled','pending_off','phase','images_enabled']},
            'ack_age_seconds':round(max(0,time.time()-control['control_acked_at']),1),
            'ledger':{'events':total,'status_counts':counts,'db_bytes':path.stat().st_size,'pages':pages,'free_pages':free},
            'ledger_changed':False,'retention_action':'none'}
    state=home/'service_status.json'
    if state.exists():
        value=json.loads(state.read_text());result['service']={k:value.get(k) for k in ['phase','blue_available','nas_ready','images_ready','desired_enabled','enabled','images_enabled','last_failure']}
        result['service']['age_seconds']=round(max(0,time.time()-value.get('updated',0)),1)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,default=Path.home()/'Library/Application Support/imessage-text-bridge');args=parser.parse_args()
    try:print(json.dumps(snapshot(args.home),ensure_ascii=True,indent=2))
    except Exception:raise SystemExit('Read-only diagnostics unavailable; details suppressed') from None
