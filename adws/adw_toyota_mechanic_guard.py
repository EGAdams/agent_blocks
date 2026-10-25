"""Toyota's mechanic: the safety net around an agent that edits Toyota's own code.

Not a workflow -- the library adw_toyota_mechanic.py imports. It sits in adws/
rather than adw_modules/ because adw_modules/ is shared machinery that is not
edited from this workspace, and it is named adw_*.py so sssf.config.yaml's
protected_files covers it for every SSSF agent too.

The agent only ever works in a sandbox copy. Nothing reaches the live tree
unless the guard is clean, the tests are green with no fewer tests than before,
and every live file the change touches still matches the snapshot it started
from. Applied changes keep a backup so a failed restart can be undone.

Ported from /home/adamsl/rol_finances/adws/adw_modules/toyota_mechanic/ (retired).
Two changes: PROTECTED names the mechanic's own agent folders now that its
definition lives in receptionist_team/agents/, and the sandbox carries
sssf_interfaces plus a read-only mirror of adws/, without which almost every
suite failed at import inside the sandbox and the gate could never pass.
"""
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PACKAGES = ('receptionist_team', 'finance_team', 'agent_memory', 'sssf_interfaces', 'tests')
SKIP_DIRS = {'__pycache__', 'lance_data', '.pytest_cache'}
SKIP_FILES = {'voice_metrics.log'}
# The mechanic must not rewrite the machinery that defines or checks its own work.
# A trailing slash protects everything under that folder. The workflow and this
# guard need no entry: they live in adws/, which is never part of the sandbox.
PROTECTED = ('finance_team/roles.py',
             'receptionist_team/agents/toyota_mechanic/',
             'receptionist_team/agents/mechanic_coder/')
# What of adws/ the sandbox gets, so tests that find it beside lancedb_memory/
# (parents[3] / 'adws') still find it there. Read-only by contract: a change to
# the mirror is refused, since the gate would otherwise pass against an ADW the
# live tree does not have.
MIRROR_DIRS = ('adw_modules', 'adw_sssf_config')
MANIFEST = 'manifest.json'


class ApplyConflict(Exception):
    pass


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path):
    for package in PACKAGES:
        base = root / package
        if not base.exists():
            continue
        for path in sorted(base.rglob('*')):
            if path.is_file() and not (SKIP_DIRS & set(path.parts)) and path.name not in SKIP_FILES \
                    and path.suffix != '.pyc':
                yield path.relative_to(root).as_posix()


def snapshot(live: Path, box: Path) -> dict[str, str]:
    hashes = {}
    for rel in _files(live):
        target = box / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live / rel, target)
        hashes[rel] = _digest(live / rel)
    return hashes


def _mirror_files(adws: Path):
    yield from (p.name for p in sorted(adws.glob('*.py')))
    for name in MIRROR_DIRS:
        for path in sorted((adws / name).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                yield path.relative_to(adws).as_posix()


def mirror_adws(adws: Path, target: Path) -> dict[str, str]:
    """Copy the ADW scripts and their machinery (not adw_data/, not the visualizer) for the sandbox's tests."""
    hashes = {}
    for rel in _mirror_files(adws):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(adws / rel, target / rel)
        hashes[rel] = _digest(adws / rel)
    return hashes


def mirror_changed(hashes: dict[str, str], target: Path) -> list[str]:
    """Mirrored files the agent modified or deleted. Files added (a test's output) are not a change."""
    return sorted(rel for rel, digest in hashes.items()
                  if not (target / rel).is_file() or _digest(target / rel) != digest)


def diff(hashes: dict[str, str], box: Path) -> Changes:
    now = {rel: _digest(box / rel) for rel in _files(box)}
    return Changes(added=sorted(set(now) - set(hashes)),
                   modified=sorted(rel for rel in now if rel in hashes and now[rel] != hashes[rel]),
                   deleted=sorted(set(hashes) - set(now)))


def is_protected(rel: str) -> bool:
    return any(rel == p or (p.endswith('/') and rel.startswith(p)) for p in PROTECTED)


def violations(changes: Changes) -> list[str]:
    problems = [f'{rel}: protected, the mechanic may not change it'
                for rel in changes.added + changes.modified + changes.deleted if is_protected(rel)]
    problems += [f'{rel}: deletions are not allowed' for rel in changes.deleted if not is_protected(rel)]
    return problems


def apply_changes(changes: Changes, box: Path, live: Path, backup: Path, hashes: dict[str, str]) -> list[str]:
    for rel in changes.modified + changes.deleted:
        if not (live / rel).exists() or _digest(live / rel) != hashes[rel]:
            raise ApplyConflict(f'{rel} changed in the live tree after the snapshot was taken')
    for rel in changes.added:
        if (live / rel).exists():
            raise ApplyConflict(f'{rel} appeared in the live tree after the snapshot was taken')
    backup.mkdir(parents=True, exist_ok=True)
    for rel in changes.modified:
        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live / rel, backup / rel)
    (backup / MANIFEST).write_text(json.dumps({'added': changes.added, 'modified': changes.modified}))
    for rel in changes.added + changes.modified:
        (live / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(box / rel, live / rel)
    return changes.added + changes.modified


def restore(live: Path, backup: Path) -> None:
    manifest = json.loads((backup / MANIFEST).read_text())
    for rel in manifest['modified']:
        shutil.copy2(backup / rel, live / rel)
    for rel in manifest['added']:
        (live / rel).unlink(missing_ok=True)


def parse_suite(output: str) -> tuple[int, int]:
    found = re.findall(r'(\d+)/(\d+) passed', output)
    return (int(found[-1][0]), int(found[-1][1])) if found else (0, 0)


def gate_passes(results: list[tuple[int, int]], baseline_total: int) -> bool:
    total = sum(t for _, t in results)
    return bool(results) and all(p == t and t > 0 for p, t in results) and total >= baseline_total


def escaped(hashes: dict[str, str], live: Path) -> Changes:
    """What changed in the LIVE tree since the snapshot (the agent wrote outside its sandbox)."""
    now = {rel: _digest(live / rel) for rel in _files(live)}
    return Changes(added=sorted(set(now) - set(hashes)),
                   modified=sorted(rel for rel in now if rel in hashes and now[rel] != hashes[rel]),
                   deleted=sorted(set(hashes) - set(now)))


def undo_escape(leak: Changes, orig: Path, live: Path) -> None:
    """Put the live tree back to the snapshot using the pristine copy."""
    for rel in leak.modified + leak.deleted:
        (live / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(orig / rel, live / rel)
    for rel in leak.added:
        (live / rel).unlink()
