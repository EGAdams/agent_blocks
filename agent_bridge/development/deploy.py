"""Local reviewed deployment; no bridge operation can invoke this tool.

plan prints the exact diff and its digest. apply requires that reviewed digest,
checks the original file hashes, runs the trusted jailed gate, then copies only
the reviewed source files. No service control, credentials or configuration edits.
"""
import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PRODUCTION = Path("/home/adamsl/agent_blocks")
DEVELOPMENT = Path("/home/adamsl/bridge_workspaces/agent_blocks_dev")
BASELINE = Path.home() / ".cache/agent_bridge/development_baseline.json"
sys.path.insert(0, str(PRODUCTION / "lancedb_memory"))
from agent_comms.executors.jail import BwrapJail, WorkspacePrivatePaths, PRIVATE_NAME
from agent_bridge_worker.adapters.jailed_checks import JailedCheckRunner
from agent_bridge_worker.redaction import SecretScanner
from agent_bridge_worker.adapters.development_jail import development_check_jail

ALLOWED_ROOTS = ("lancedb_memory/agent_memory/", "lancedb_memory/receptionist_team/",
                 "lancedb_memory/agent_comms/", "docs/", "adws/")
PROTECTED = ("adws/adw_data/", "adws/adw_bridge_worker.py", "lancedb_memory/agent_comms/factory.py")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jail():
    return development_check_jail()


def plan():
    baseline = json.loads(BASELINE.read_text())
    changes = []
    names = set(baseline)
    for prefix in ALLOWED_ROOTS:
        names.update(str(p.relative_to(DEVELOPMENT)) for p in (DEVELOPMENT / prefix).rglob("*") if p.is_file())
    for name in sorted(names):
        if not name.startswith(ALLOWED_ROOTS): continue
        source, target = DEVELOPMENT / name, PRODUCTION / name
        if source.is_symlink() or target.is_symlink() or source.resolve() != source or target.resolve() != target:
            raise ValueError("symlink refused")
        if not source.is_file():
            if name in baseline: raise ValueError("deletions require separate review: " + name)
            continue
        new = source.read_bytes()
        if digest(new) == baseline.get(name): continue
        if (name.startswith(PROTECTED) or source.suffix not in {".py", ".md", ".js", ".css", ".html"}
                or any(PRIVATE_NAME.search(part) for part in Path(name).parts)):
            raise ValueError("configuration or protected path refused: " + name)
        old = target.read_bytes() if target.is_file() else b""
        if digest(old) != baseline.get(name, digest(b"")):
            raise ValueError("production changed since baseline: " + name)
        text = new.decode("utf-8")
        if SecretScanner().found(text): raise ValueError("possible credential; deployment withheld")
        changes.append({"path": name, "old": digest(old), "new": digest(new), "exists": target.exists(),
                        "diff": "".join(difflib.unified_diff(old.decode().splitlines(True), text.splitlines(True),
                                                   fromfile="production/"+name, tofile="development/"+name))})
    encoded = json.dumps(changes, sort_keys=True).encode()
    return changes, digest(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "apply"])
    parser.add_argument("--reviewed-digest")
    args = parser.parse_args()
    changes, review = plan()
    if args.operation == "plan":
        print(json.dumps({"digest": review, "changes": changes}, indent=2))
        return
    if args.reviewed_digest != review: raise SystemExit("Reviewed digest does not match current changes")
    if not changes: raise SystemExit("No changes to deploy")
    gate = jail()
    result = JailedCheckRunner(lambda argv, root: gate.wrap(argv, root, root, [])).run(
        DEVELOPMENT, ["/opt/bridge-test-venv/bin/python", "-B", "/opt/bridge-check.py"])
    if not result.passed: raise SystemExit("Gate failed: " + result.output_tail)
    if plan()[1] != review: raise SystemExit("Files changed during verification")
    backup = BASELINE.parent / ("deployment_" + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    # Persist backups and the reviewed manifest before changing any source file.
    (backup / "manifest.json").write_text(json.dumps(changes, indent=2))
    for change in changes:
        target = PRODUCTION / change["path"]
        if target.exists():
            saved = backup / change["path"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    baseline = json.loads(BASELINE.read_text())
    for change in changes:
        target = PRODUCTION / change["path"]
        source = DEVELOPMENT / change["path"]
        data = source.read_bytes()
        old = target.read_bytes() if target.is_file() else b""
        if digest(data) != change["new"] or digest(old) != change["old"]:
            raise SystemExit("Concurrent change refused; earlier files may have deployed; see backup " + str(backup))
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, "wb") as stream: stream.write(data)
        os.chmod(temporary, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.replace(temporary, target)
        baseline[change["path"]] = change["new"]
    BASELINE.write_text(json.dumps(baseline, indent=2))
    os.chmod(BASELINE, 0o600)
    print(json.dumps({"deployed": [c["path"] for c in changes], "backup": str(backup), "services_restarted": False}))


if __name__ == "__main__": main()
