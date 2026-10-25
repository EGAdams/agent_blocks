#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Toyota's mechanic: diagnose or enhance Toyota's own code, safely.

Usage (from /home/adamsl/agent_blocks):
    lancedb_memory/.venv/bin/python adws/adw_toyota_mechanic.py "<request>" [--diagnose]
    lancedb_memory/.venv/bin/python adws/adw_toyota_mechanic.py --watchdog <adw_id>          (internal, after an apply)
    lancedb_memory/.venv/bin/python adws/adw_toyota_mechanic.py --coder <sandbox> <task.md>  (internal, one coding turn)

Phases: engineer(request) -> snapshot -> baseline -> mechanic -> verify (fix loop)
        -> guard -> apply -> restart (detached watchdog; rolls back if she does not come back)

The coding agent only ever edits a sandbox copy. A change reaches the live tree
only if the guard is clean, every suite is green with no fewer tests than the
baseline, and no live file moved since the snapshot. --diagnose never applies.

Run under lancedb_memory's venv, not uv: --coder imports finance_team and
receptionist_team, which only that interpreter can load.

The agents themselves are folders, not literals here:
    receptionist_team/agents/toyota_mechanic/  the dispatcher Toyota routes to (runs this file)
    receptionist_team/agents/mechanic_coder/   the coder --coder runs inside the sandbox
Both are in the guard's PROTECTED list, so a mechanic run cannot rewrite either.

Ported from /home/adamsl/rol_finances/adws/adw_toyota_mechanic.py (retired).
"""
import argparse
import asyncio
import dataclasses
import fcntl
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adw_modules import agents, session
from adw_modules.data_types import PhaseParams
from adw_toyota_mechanic_guard import (ApplyConflict, apply_changes, diff, escaped, gate_passes, mirror_adws,
                                       mirror_changed, parse_suite, restore, snapshot, undo_escape, violations)

REPO = Path(__file__).resolve().parents[1]
ADWS = REPO / 'adws'
LIVE = REPO / 'lancedb_memory'
PYTHON = LIVE / '.venv/bin/python'
DATA = ADWS / 'adw_data/mechanic'
CODER = 'mechanic_coder'
SERVICE, HEALTH = 'toyota-receptionist', 'http://127.0.0.1:8767/health'
MAX_FIX_ROUNDS = 2
# Must outlast the mechanic role's own LLM call to summarize "DONE: ..." plus
# the HTTP write of that reply -- see the 'restart' step below for the race
# this delay exists to avoid.
RESTART_DELAY_SECONDS = 30
PLAN_SOURCES = ['/home/adamsl/letta-code/dashboard/js/plans/voice-communication/specs',
                'receptionist_team/CLAUDE.md (relative, inside the sandbox)']


def run_suites(box: Path) -> tuple[list[tuple[int, int]], str]:
    """Every tests/test_*.py module in the sandbox, so tests the mechanic adds are gated too."""
    results, report = [], []
    for path in sorted((box / 'receptionist_team/tests').glob('test_*.py')):
        module = f'receptionist_team.tests.{path.stem}'
        done = subprocess.run([str(PYTHON), '-m', module], cwd=box, capture_output=True, text=True, timeout=600,
                              env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(box), 'PYTHONUNBUFFERED': '1',
                                   'HOME': str(Path.home())})
        output = done.stdout + done.stderr
        results.append(parse_suite(output))
        report.append(f'== {module} ({results[-1][0]}/{results[-1][1]}) ==\n{output[-1500:]}')
    return results, '\n'.join(report)


def ask_mechanic(box: Path, task: Path) -> str:
    done = subprocess.run([str(PYTHON), str(Path(__file__).resolve()), '--coder', str(box), str(task)], cwd=REPO,
                          capture_output=True, text=True, timeout=3000)
    return (done.stdout + done.stderr)[-4000:]


def coder(box: str, task_file: str) -> int:
    """One coding turn in the sandbox, defined by the live mechanic_coder folder -- never the sandbox's copy."""
    sys.path.insert(0, str(LIVE))
    from finance_team.adapters.tool_agent import run_role
    from receptionist_team.agents import AgentRoleFactory, catalog

    definition = catalog().definition(CODER)
    role = AgentRoleFactory().role(definition, {'task': Path(task_file).read_text()})
    role = dataclasses.replace(role, output_path=box)
    model = definition.model_configuration().model
    if not model:
        print(f'{CODER}/model_confiurations/model.json names no model')
        return 1
    result = asyncio.run(run_role(role, box, model, echo=False))
    print(result.final_text)
    return 1 if result.is_error else 0


def healthy(timeout: float = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if urllib.request.urlopen(HEALTH, timeout=3).status == 200:
                return True
        except Exception:
            time.sleep(3)
    return False


def watchdog(adw_id: str) -> int:
    backup, outcome = DATA / adw_id / 'backup', DATA / adw_id / 'outcome.json'
    subprocess.run(['systemctl', '--user', 'restart', SERVICE])
    if healthy():
        outcome.write_text(json.dumps({'status': 'applied_and_healthy'}))
        return 0
    restore(LIVE, backup)
    subprocess.run(['systemctl', '--user', 'restart', SERVICE])
    outcome.write_text(json.dumps({'status': 'rolled_back', 'service_back': healthy()}))
    return 1


def main(request: str, diagnose: bool, config: str, adw_id: str | None) -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    lock = (DATA / 'mechanic.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('BUSY: another mechanic run is already in progress')
        return 3
    run = session.ensure(agents.load_config(config), adw_id)
    work = DATA / run.adw_id
    # tree/ has the live layout -- lancedb_memory/ beside adws/ -- because tests
    # find the ADWs at parents[3] / 'adws'; box is the part the agent edits.
    tree = work / 'tree'
    box, backup = tree / 'lancedb_memory', work / 'backup'

    def step(name, description):
        return run.phase(PhaseParams(name=name, kind='code', owner='toyota_mechanic', description=description))

    with run.phase(PhaseParams(name='request', kind='engineer', owner=run.engineer,
                               description='Capture the incoming ask')) as ph:
        ph.log(input=request, diagnose=diagnose)

    with step('snapshot', 'Copy Toyota\'s source into a sandbox; the live tree is never edited by the agent') as ph:
        hashes = snapshot(LIVE, box)
        snapshot(LIVE, work / 'orig')
        mirrored = mirror_adws(ADWS, tree / 'adws')
        ph.log(files=len(hashes), mirrored=len(mirrored), sandbox=str(box))

    with step('baseline', 'Run every suite before any change') as ph:
        results, _ = run_suites(box)
        baseline_total = sum(t for _, t in results)
        baseline_green = gate_passes(results, 0)
        ph.log(suites=len(results), tests=baseline_total, green=baseline_green)

    task, report = work / 'task.md', ''
    task.write_text(f"""# Request
{request}

# Mode
{'DIAGNOSE ONLY: change nothing; report what is wrong and the smallest fix.' if diagnose else 'Make the change, test first.'}

# Baseline
{'All suites are green.' if baseline_green else 'The suites are NOT all green before you start; fixing that is part of the job.'}
Plan sources (read only): {', '.join(PLAN_SOURCES)}
""")
    with step('mechanic', 'The coding agent works inside the sandbox only') as ph:
        report = ask_mechanic(box, task)
        ph.log(report=report[-800:])

    leak = escaped(hashes, LIVE)
    if not leak.empty:
        undo_escape(leak, work / 'orig', LIVE)
        print('REFUSED: the agent wrote outside its sandbox (' + ', '.join(leak.added + leak.modified + leak.deleted)
              + '); the live files were put back')
        return run.finish(accepted=False, reason='sandbox escape undone')
    changes = diff(hashes, box)
    if diagnose or changes.empty:
        print(f'{"DIAGNOSIS" if diagnose else "NO_CHANGE"}: {report.strip()[-1800:]}')
        return run.finish(accepted=True, reason='Nothing applied')

    for round_number in range(1, MAX_FIX_ROUNDS + 2):
        with step(f'verify_{round_number}', 'Every suite must be green with no fewer tests than the baseline') as ph:
            results, output = run_suites(box)
            ok = gate_passes(results, baseline_total)
            ph.log(green=ok, tests=sum(t for _, t in results))
        if ok or round_number > MAX_FIX_ROUNDS:
            break
        task.write_text(task.read_text() + f'\n# Test failures, round {round_number}\nFix these without deleting tests.\n{output}\n')
        with step(f'fix_{round_number}', 'Send the verbatim failures back to the agent') as ph:
            ask_mechanic(box, task)
    changes = diff(hashes, box)

    with step('guard', 'Protected files, deletions and edits to the adws/ mirror are refused') as ph:
        problems = violations(changes)
        problems += [f'adws/{rel}: the ADW mirror is read-only, the mechanic may not change it'
                     for rel in mirror_changed(mirrored, tree / 'adws')]
        ph.log(problems=problems)
    if problems or not ok:
        why = problems or ['the suites are not green after the fix attempts']
        print('REFUSED: ' + '; '.join(why))
        print(f'SANDBOX: {box}')
        return run.finish(accepted=False, reason='; '.join(why))

    with step('apply', 'Copy the change into the live tree, with a backup') as ph:
        try:
            applied = apply_changes(changes, box, LIVE, backup, hashes)
        except ApplyConflict as conflict:
            ph.log(conflict=str(conflict))
            print(f'REFUSED: {conflict}')
            return run.finish(accepted=False, reason=str(conflict))
        ph.log(files=applied)

    with step('restart', 'A detached watchdog restarts Toyota and rolls back if she does not come back') as ph:
        # 5s used to race the reply: this process still has to let the mechanic
        # role turn "DONE: ..." into spoken text and stream it back over the open
        # HTTP connection before systemctl kills this very process. That LLM call
        # plus the write can take well over 5s, so the restart was landing mid-
        # response and the client saw the connection die with no reply at all.
        # RESTART_DELAY_SECONDS gives that round trip real headroom.
        subprocess.run(['systemd-run', '--user', '--collect', f'--on-active={RESTART_DELAY_SECONDS}',
                        str(PYTHON), str(Path(__file__).resolve()), '--watchdog', run.adw_id],
                       check=True, capture_output=True)
    print(f'DONE: {len(applied)} file(s) changed, {sum(t for _, t in results)} tests green (was {baseline_total})')
    for rel in applied:
        print(f'CHANGED: {rel}')
    print(f'RESTART: Toyota restarts in about {RESTART_DELAY_SECONDS} seconds (after she replies) and rolls back by herself if she does not come back healthy')
    return run.finish(accepted=True, reason='Applied; restart handed to the watchdog')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('request', nargs='?')
    parser.add_argument('--diagnose', action='store_true')
    parser.add_argument('--watchdog', metavar='ADW_ID')
    parser.add_argument('--coder', nargs=2, metavar=('SANDBOX', 'TASK_MD'))
    parser.add_argument('--config', default=str(ADWS / 'adw_sssf_config/sssf.config.yaml'))
    parser.add_argument('--adw-id', default=None)
    args = parser.parse_args()
    if args.watchdog:
        sys.exit(watchdog(args.watchdog))
    if args.coder:
        sys.exit(coder(*args.coder))
    if not args.request:
        parser.error('a request is required')
    sys.exit(main(args.request, args.diagnose, args.config, args.adw_id))
