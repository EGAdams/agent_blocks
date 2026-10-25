"""The mechanic's safety net: snapshot -> diff -> guard -> apply -> restore.

Ported from rol_finances' adws/tests/test_toyota_mechanic.py (pytest, which
this venv does not have) when the workflow moved into agent_blocks, plus what
the move added: PROTECTED must cover the folders the mechanic is defined in,
and the sandbox must carry what the suites import, or the gate never passes.
Filesystem only, in temp dirs -- no model, no service.
"""
import sys
import tempfile
from pathlib import Path

# The guard lives in the workspace's adws/ directory, reached by path, same as
# test_live_finance_adw. Inside a mechanic sandbox, adws/ is the mirror beside it.
ADWS_DIR = Path(__file__).resolve().parents[3] / "adws"
if str(ADWS_DIR) not in sys.path:
    sys.path.insert(0, str(ADWS_DIR))

import adw_toyota_mechanic_guard as tm  # noqa: E402
from adw_toyota_mechanic_guard import (ApplyConflict, Changes, apply_changes, diff, gate_passes,  # noqa: E402
                                       parse_suite, restore, snapshot, violations)

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def make_live(tmp: Path) -> Path:
    return make_tree(tmp / 'live', {
        'receptionist_team/roles.py': 'A', 'receptionist_team/tests/test_a.py': 'T',
        'receptionist_team/agents/toyota_mechanic/system_prompt_template/system_prompt.md': 'D',
        'finance_team/roles.py': 'R', 'sssf_interfaces/__init__.py': 'S',
        'receptionist_team/__pycache__/x.pyc': 'junk', 'receptionist_team/voice_metrics.log': 'noise'})


async def test_a_snapshot_copies_source_and_skips_caches_and_logs():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        box = tmp / 'box'
        hashes = snapshot(make_live(tmp), box)
        assert sorted(hashes) == ['finance_team/roles.py',
                                  'receptionist_team/agents/toyota_mechanic/system_prompt_template/system_prompt.md',
                                  'receptionist_team/roles.py', 'receptionist_team/tests/test_a.py',
                                  'sssf_interfaces/__init__.py']
        assert not (box / 'receptionist_team/__pycache__').exists()


async def test_the_sandbox_carries_every_package_the_suites_import():
    # receptionist_team.agents imports sssf_interfaces; without it nearly every
    # suite failed at import in the sandbox and the gate could never pass.
    for package in ('receptionist_team', 'finance_team', 'agent_memory', 'sssf_interfaces', 'tests'):
        assert package in tm.PACKAGES, package


async def test_diff_reports_added_modified_and_deleted():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        box = tmp / 'box'
        hashes = snapshot(make_live(tmp), box)
        (box / 'receptionist_team/roles.py').write_text('B')
        (box / 'receptionist_team/new.py').write_text('N')
        (box / 'receptionist_team/tests/test_a.py').unlink()
        changes = diff(hashes, box)
        assert (changes.added, changes.modified, changes.deleted) == (
            ['receptionist_team/new.py'], ['receptionist_team/roles.py'], ['receptionist_team/tests/test_a.py'])


async def test_the_guard_refuses_protected_files_and_deleted_tests():
    problems = violations(Changes(added=[], modified=['finance_team/roles.py'],
                                  deleted=['receptionist_team/tests/test_a.py']))
    assert len(problems) == 2
    assert violations(Changes(added=['receptionist_team/new.py'], modified=['receptionist_team/roles.py'],
                              deleted=[])) == []


async def test_the_guard_refuses_any_file_in_the_mechanics_own_folders():
    for rel in ('receptionist_team/agents/toyota_mechanic/user_prompt_template/user_prompt.md',
                'receptionist_team/agents/mechanic_coder/system_prompt_template/system_prompt.md',
                'receptionist_team/agents/mechanic_coder/model_confiurations/model.json',
                'receptionist_team/agents/mechanic_coder/anything_new.md'):
        assert violations(Changes(added=[], modified=[rel], deleted=[])), rel
        assert violations(Changes(added=[rel], modified=[], deleted=[])), rel
    # A prefix match, not a substring one: a sibling folder is not protected.
    assert violations(Changes(added=['receptionist_team/agents/toyota_mechanic_extra/x.md'],
                              modified=[], deleted=[])) == []


async def test_protected_names_the_folders_the_mechanic_is_really_defined_in():
    # If either folder moves, PROTECTED must move with it in the same commit --
    # otherwise the mechanic can quietly rewrite its own instructions.
    for name in ('toyota_mechanic', 'mechanic_coder'):
        prompt = AGENTS_DIR / name / 'system_prompt_template' / 'system_prompt.md'
        assert prompt.is_file(), prompt
        rel = prompt.relative_to(AGENTS_DIR.parents[1]).as_posix()
        assert tm.is_protected(rel), rel


async def test_apply_copies_changes_backs_up_and_restore_undoes_it():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        live, box, backup = make_live(tmp), tmp / 'box', tmp / 'backup'
        hashes = snapshot(live, box)
        (box / 'receptionist_team/roles.py').write_text('B')
        (box / 'receptionist_team/new.py').write_text('N')
        apply_changes(diff(hashes, box), box, live, backup, hashes)
        assert (live / 'receptionist_team/roles.py').read_text() == 'B' and (live / 'receptionist_team/new.py').exists()
        restore(live, backup)
        assert (live / 'receptionist_team/roles.py').read_text() == 'A'
        assert not (live / 'receptionist_team/new.py').exists()


async def test_apply_refuses_when_the_live_file_moved_on_since_the_snapshot():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        live, box = make_live(tmp), tmp / 'box'
        hashes = snapshot(live, box)
        (box / 'receptionist_team/roles.py').write_text('B')
        (live / 'receptionist_team/roles.py').write_text('someone else')
        try:
            apply_changes(diff(hashes, box), box, live, tmp / 'backup', hashes)
        except ApplyConflict:
            pass
        else:
            raise AssertionError('expected ApplyConflict')
        assert (live / 'receptionist_team/roles.py').read_text() == 'someone else'


async def test_a_new_file_that_appeared_live_is_a_conflict_too():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        live, box = make_live(tmp), tmp / 'box'
        hashes = snapshot(live, box)
        (box / 'receptionist_team/new.py').write_text('N')
        (live / 'receptionist_team/new.py').write_text('theirs')
        try:
            apply_changes(diff(hashes, box), box, live, tmp / 'backup', hashes)
        except ApplyConflict:
            pass
        else:
            raise AssertionError('expected ApplyConflict')


async def test_suite_output_is_parsed_and_the_gate_wants_green_and_no_fewer_tests():
    assert parse_suite('PASS a\n\n15/15 passed\n') == (15, 15)
    assert parse_suite('boom') == (0, 0)
    assert gate_passes([(15, 15), (3, 3)], baseline_total=18)
    assert not gate_passes([(14, 15), (3, 3)], baseline_total=18)
    assert not gate_passes([(10, 10), (3, 3)], baseline_total=18)


async def test_a_write_to_the_live_tree_is_detected_and_undone():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        live, box, orig = tmp / 'live', tmp / 'box', tmp / 'orig'
        (live / 'receptionist_team').mkdir(parents=True)
        (live / 'receptionist_team/a.py').write_text('one')
        hashes = tm.snapshot(live, box)
        tm.snapshot(live, orig)
        (live / 'receptionist_team/a.py').write_text('two')
        (live / 'receptionist_team/new.py').write_text('x')
        leak = tm.escaped(hashes, live)
        assert leak.modified == ['receptionist_team/a.py'] and leak.added == ['receptionist_team/new.py']
        tm.undo_escape(leak, orig, live)
        assert (live / 'receptionist_team/a.py').read_text() == 'one'
        assert not (live / 'receptionist_team/new.py').exists() and tm.escaped(hashes, live).empty


async def test_the_adws_mirror_skips_run_data_and_reports_edits_but_not_test_output():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        adws = make_tree(tmp / 'adws', {
            'adw_x.py': 'X', 'adw_modules/session.py': 'S', 'adw_sssf_config/sssf.config.yaml': 'C',
            'adw_data/sessions/big.json': 'run data', 'visualizer/node_modules/huge.js': 'nope'})
        mirror = tmp / 'tree/adws'
        hashes = tm.mirror_adws(adws, mirror)
        assert sorted(hashes) == ['adw_modules/session.py', 'adw_sssf_config/sssf.config.yaml', 'adw_x.py']
        assert tm.mirror_changed(hashes, mirror) == []
        (mirror / 'adw_data/spreadsheets').mkdir(parents=True)
        (mirror / 'adw_data/spreadsheets/out.xlsx').write_text('a test wrote this')
        assert tm.mirror_changed(hashes, mirror) == []
        (mirror / 'adw_x.py').write_text('edited')
        (mirror / 'adw_modules/session.py').unlink()
        assert tm.mirror_changed(hashes, mirror) == ['adw_modules/session.py', 'adw_x.py']


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
