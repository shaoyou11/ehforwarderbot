"""Ordered host startup: shared transport first, selected frontend by worker."""
import json
import subprocess
import sys
import time
from pathlib import Path


def boot(config,action):
    def run(args):return subprocess.check_output(args,stderr=subprocess.DEVNULL,text=True,timeout=50).strip()
    if action=='start':
        run(['docker','start',config['bot_api_container']])
        end=time.monotonic()+120
        while time.monotonic()<end:
            if run(['docker','inspect','-f','{{.State.Health.Status}}',config['bot_api_container']])=='healthy':return
            time.sleep(2)
        raise RuntimeError('shared Bot API readiness timeout')
    elif action=='stop':
        for name in [*config['containers'].values(),config['watchdog'],config.get('native_aux_container'),config['native_container'],config['bot_api_container']]:
            if name:run(['docker','stop','-t','30',name])
    else:raise ValueError('unknown action')

if __name__=='__main__':boot(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2])
