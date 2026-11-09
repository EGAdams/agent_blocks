"""ISourceTree over the git command line. It fetches, fast-forwards and moves HEAD back with --keep;
it never commits, pushes, stashes, cleans or resets --hard, so nobody's uncommitted work is ever lost."""
from __future__ import annotations

import subprocess
from pathlib import Path

SHA = 40


class GitError(RuntimeError):
    pass


class GitSourceTree:
    def __init__(self, git: str = "git", timeout: int = 120) -> None:
        self._git, self._timeout = git, timeout

    def _run(self, root: Path, *args: str) -> str:
        done = subprocess.run([self._git, "-C", str(root), *args], capture_output=True, text=True,
                              timeout=self._timeout, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            raise GitError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()[:400]}")
        return done.stdout

    def head(self, root: Path) -> str | None:
        try:
            return self._run(root, "rev-parse", "HEAD").strip()
        except GitError:
            return None

    def dirty(self, root: Path) -> list[str]:
        out = self._run(root, "status", "--porcelain", "-z", "--untracked-files=all")
        paths, items = [], out.split("\0")
        skip = False
        for item in items:
            if skip:
                skip = False
                continue
            if len(item) < 4:
                continue
            paths.append(item[3:])
            if item[0] in "RC":            # a rename is followed by its old name
                skip = True
        return paths

    def fetch(self, root: Path, remote: str, branch: str) -> str:
        self._run(root, "fetch", "--quiet", remote, branch)
        return self._run(root, "rev-parse", f"{remote}/{branch}").strip()

    def changed(self, root: Path, old: str, new: str) -> list[str]:
        return [line for line in self._run(root, "diff", "--name-only", old, new).splitlines() if line]

    def fast_forward(self, root: Path, commit: str) -> None:
        self._run(root, "merge", "--ff-only", "--quiet", commit)

    def reset_keep(self, root: Path, commit: str) -> None:
        if len(commit) != SHA or not all(c in "0123456789abcdef" for c in commit):
            raise GitError("not a commit id")
        self._run(root, "reset", "--keep", commit)
