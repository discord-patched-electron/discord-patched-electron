"""Reserve content-keyed work before reusable jobs start; no duplicate dispatches."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from common import ROOT, read_json, write_json
from update_policy import plan

PROJECT = 'discord-patched-electron/discord-patched-electron'


def schedule(state, policy, sources, now, run_id, results, compiler_busy=False):
    work = state.setdefault('client_work', {})
    for item in [*work.values(), *state.setdefault('electron_work', {}).values()]:
        if item['status'] == 'queued' and results.get(item['run']):
            item['status'] = results[item['run']]
    decision = plan(state, policy, sources, now)
    for item in decision['clients']:
        work[item['key']] = {**item, 'status':'queued', 'run':run_id, 'requested_at':now}
    native = decision['electron']
    if (native['action'] == 'prepare-source' and not compiler_busy
        and native['runtime_key'] not in state['electron_work']):
        state['electron_work'][native['runtime_key']] = {
            'status':'queued', 'run':run_id, 'target':native['target'], 'requested_at':now}
        state['last_compiler_attempt_at'] = now
    else:
        native['action'] = 'deferred' if compiler_busy else 'no-new-work'
    return decision


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY') != PROJECT:
        raise ValueError('Scheduling is limited to the project workflow')
    def api(endpoint):
        return json.loads(subprocess.check_output(['gh','api',f'repos/{PROJECT}/{endpoint}'], text=True, timeout=60))
    state = read_json(args.state)
    run_id = os.environ['GITHUB_RUN_ID']
    results = {}
    for item in [*state.get('client_work', {}).values(), *state.get('electron_work', {}).values()]:
        if item['status'] == 'queued' and item['run'] != run_id and item['run'] not in results:
            result = api('actions/runs/' + item['run'])
            results[item['run']] = result['conclusion'] if result['status'] == 'completed' else None
    runs = api('actions/workflows/build.yml/runs?per_page=20')['workflow_runs']
    busy = (any(r['status'] != 'completed' for r in runs)
        or not read_json(ROOT/'config/channels.json')['compiler']['enabled'])
    now = datetime.now(timezone.utc).isoformat()
    result = schedule(state, read_json(ROOT/'config/update-policy.json'),
        read_json(ROOT/'config/sources.lock.json'), now, run_id, results, busy)
    write_json(args.state, state)
    write_json(ROOT/'out/discovery/update-plan.json', result)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('clients=' + json.dumps([x['channel'] for x in result['clients']]) + '\n')
        output.write('electron=' + (result['electron'].get('runtime_key', '')
            if result['electron']['action'] == 'prepare-source' else '') + '\n')
        output.write('reference_run=' + read_json(ROOT/'config/update-policy.json')['last_good_runtime']['run'] + '\n')
