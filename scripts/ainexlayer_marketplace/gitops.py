"""Git operations for the registry workflows, behind one small wrapper.

Every call is a fixed argument list (no shell). Two rules make this safe to
hand to the publish pipeline:

* ``reset_to`` discards local work, so it only runs where that is harmless:
  inside a GitHub Actions runner's throwaway checkout, or when a test passes
  ``allow_reset=True`` for a repository it created in a temporary folder. On
  a developer machine it refuses.
* The push token is given to ``push`` only, through per-process git config
  environment variables. It never appears in argv, in ``.git/config`` or in
  an error message.

``after_reset`` callbacks run after every reset. Intake uses one to re-write
the submission file, because a retry starts again from a fresh ``main`` that
does not have it yet.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Callable

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


class GitError(RuntimeError):
    pass


class PushRejected(GitError):
    """The remote refused a push because it is not a fast-forward."""


def _check_ref(ref: str) -> str:
    if not _REF_RE.fullmatch(ref or "") or ".." in ref or ref.startswith("-"):
        raise GitError(f"unsafe ref {ref!r}")
    return ref


def _check_path(path: str) -> str:
    if not path or path.startswith(("/", "-")) or ".." in path.split("/") or "\\" in path or ":" in path:
        raise GitError(f"unsafe path {path!r}")
    return path


class GitOps:
    def __init__(
        self,
        root: Path,
        *,
        remote: str = "origin",
        branch: str = "main",
        allow_reset: bool | None = None,
        after_reset: list[Callable[[], None]] | None = None,
        push_token: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.remote = _check_ref(remote)
        self.branch = _check_ref(branch)
        if allow_reset is None:
            allow_reset = os.environ.get("GITHUB_ACTIONS") == "true"
        self.allow_reset = bool(allow_reset)
        self.after_reset: list[Callable[[], None]] = list(after_reset or [])
        self._push_token = push_token or None

    def __repr__(self) -> str:  # never show the token
        return f"GitOps(root={str(self.root)!r}, remote={self.remote!r}, branch={self.branch!r})"

    # -- plumbing ---------------------------------------------------------

    def _run(
        self, args: Sequence[str], *, env: dict[str, str] | None = None, check: bool = True
    ) -> subprocess.CompletedProcess:
        full_env = dict(os.environ)
        full_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            full_env.update(env)
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            env=full_env,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace')[-500:]}")
        return proc

    # -- reads ------------------------------------------------------------

    def rev_parse(self, ref: str) -> str:
        out = self._run(["rev-parse", "--verify", f"{_check_ref(ref)}^{{commit}}"]).stdout.decode().strip()
        if not _SHA_RE.fullmatch(out):
            raise GitError("rev-parse returned no sha")
        return out

    def head(self) -> str:
        return self.rev_parse("HEAD")

    def read_blob(self, sha: str, path: str, *, max_bytes: int) -> bytes:
        """Read one file from a commit as bytes. Nothing is checked out."""
        if not _SHA_RE.fullmatch(sha or ""):
            raise GitError("expected a full commit sha")
        spec = f"{sha}:{_check_path(path)}"
        size_proc = self._run(["cat-file", "-s", spec])
        size = int(size_proc.stdout.decode().strip() or "0")
        if size > max_bytes:
            raise GitError(f"{path} is larger than {max_bytes} bytes")
        return self._run(["cat-file", "blob", spec]).stdout

    def is_ancestor(self, older: str, newer: str) -> bool:
        proc = self._run(["merge-base", "--is-ancestor", _check_ref(older), _check_ref(newer)], check=False)
        return proc.returncode == 0

    def changed_files(self, base: str, head: str, prefix: str) -> list[tuple[str, str]]:
        """``(status letter, path)`` for files under ``prefix`` changed between two commits."""
        out = self._run(
            [
                "diff",
                "--name-status",
                "--no-renames",
                "-z",
                _check_ref(base),
                _check_ref(head),
                "--",
                _check_path(prefix),
            ]
        ).stdout.decode("utf-8", "replace")
        parts = [p for p in out.split("\0") if p]
        return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]

    def status_paths(self) -> list[str]:
        out = self._run(["status", "--porcelain", "--untracked-files=all"]).stdout.decode("utf-8", "replace")
        return [line[3:] for line in out.splitlines() if line.strip()]

    # -- network ----------------------------------------------------------

    def fetch_branch(self) -> str:
        self._run(
            [
                "fetch",
                "--no-tags",
                "--quiet",
                self.remote,
                f"+refs/heads/{self.branch}:refs/remotes/{self.remote}/{self.branch}",
            ]
        )
        return self.rev_parse(f"{self.remote}/{self.branch}")

    def fetch_pull_head(self, number: int, expected_sha: str) -> str:
        """Fetch a pull request head as data and confirm it is ``expected_sha``."""
        if not _SHA_RE.fullmatch(expected_sha or ""):
            raise GitError("expected a full commit sha")
        local = f"refs/ainexlayer/pr/{int(number)}"
        self._run(["fetch", "--no-tags", "--quiet", self.remote, f"+refs/pull/{int(number)}/head:{local}"])
        got = self.rev_parse(local)
        if got != expected_sha:
            raise GitError("the pull request head moved while it was being checked")
        return got

    # -- writes -----------------------------------------------------------

    def reset_to(self, ref: str) -> None:
        if not self.allow_reset:
            raise GitError("reset is only allowed in a CI runner checkout or a test repository")
        self._run(["reset", "--hard", "--quiet", _check_ref(ref)])
        self._run(["clean", "-fdq", "--", "skills", "plugins", "agents", "submissions", "docs"])
        for callback in self.after_reset:
            callback()

    def commit(self, paths: Sequence[str], message: str) -> str | None:
        """Stage ``paths`` and commit as the bot. Returns None when nothing changed."""
        clean = [_check_path(p) for p in paths]
        # A path that neither exists nor is tracked has nothing to stage, and
        # `git add` refuses an unmatched pathspec.
        clean = [p for p in clean if (self.root / p).exists() or self._run(["ls-files", "--", p]).stdout.strip()]
        if not clean:
            return None
        self._run(["add", "--all", "--", *clean])
        if self._run(["diff", "--cached", "--quiet"], check=False).returncode == 0:
            return None
        env = {
            "GIT_AUTHOR_NAME": BOT_NAME,
            "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_NAME,
            "GIT_COMMITTER_EMAIL": BOT_EMAIL,
        }
        self._run(["commit", "--quiet", "--no-verify", "-m", message], env=env)
        return self.head()

    def push(self) -> str:
        """Push HEAD to the branch. Raises ``PushRejected`` on a non-fast-forward."""
        token = self._push_token
        env: dict[str, str] = {}
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env = {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            }
        proc = self._run(["push", "--porcelain", self.remote, f"HEAD:refs/heads/{self.branch}"], env=env, check=False)
        if proc.returncode != 0:
            err = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            if token:
                err = err.replace(token, "***")
            if "[rejected]" in err or "non-fast-forward" in err or "fetch first" in err:
                raise PushRejected("push rejected: the branch moved")
            raise GitError(f"push failed: {err[-500:]}")
        return self.head()
