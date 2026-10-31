"""Minimal GitHub REST client for the registry workflows. Stdlib only.

Only the calls the intake, gate and publish scripts need. The token is read
from the environment by the caller and never logged, never put on a command
line, and never included in an exception message.

Tests inject a fake transport (or a fake client with the same methods), so
no test ever talks to GitHub.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

API_ROOT = "https://api.github.com"
USER_AGENT = "ainexlayer-marketplace-bot"
_REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_PAGES = 30

# (method, url, headers, body) -> (status, response headers, body bytes)
Transport = Callable[[str, str, dict[str, str], Optional[bytes]], tuple[int, dict[str, str], bytes]]


class GitHubError(RuntimeError):
    """A GitHub API call failed. The message never contains the token."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API error {status}: {message}")
        self.status = status


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, dict[str, str], bytes]:
    if not url.startswith(API_ROOT + "/"):
        raise GitHubError(0, "refusing a request outside the GitHub API")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - https API host checked above
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https host
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()


class GitHubClient:
    """The handful of REST calls the registry automation makes."""

    def __init__(self, repo: str, token: str, transport: Transport | None = None) -> None:
        if not _REPO_RE.fullmatch(repo or ""):
            raise ValueError("repository must look like owner/name")
        if not token:
            raise ValueError("a token is required")
        self.repo = repo
        self._token = token
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:  # never show the token
        return f"GitHubClient(repo={self.repo!r})"

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, payload: Any = None) -> tuple[int, dict[str, str], Any]:
        url = path if path.startswith(API_ROOT + "/") else f"{API_ROOT}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, resp_headers, raw = self._transport(method, url, headers, body)
        data: Any = None
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
        if status >= 400:
            message = data.get("message") if isinstance(data, dict) else ""
            raise GitHubError(status, str(message or "request failed")[:200])
        return status, resp_headers, data

    def _paginate(self, path: str) -> list[Any]:
        items: list[Any] = []
        sep = "&" if "?" in path else "?"
        for page in range(1, _MAX_PAGES + 1):
            _, _, data = self._request("GET", f"{path}{sep}per_page=100&page={page}")
            if not isinstance(data, list):
                raise GitHubError(0, "expected a list response")
            items.extend(data)
            if len(data) < 100:
                return items
        raise GitHubError(0, "too many pages")

    def _repo_path(self, suffix: str) -> str:
        return f"/repos/{self.repo}{suffix}"

    # -- issues -----------------------------------------------------------

    def comment(self, number: int, body: str) -> None:
        self._request("POST", self._repo_path(f"/issues/{int(number)}/comments"), {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._request("POST", self._repo_path(f"/issues/{int(number)}/labels"), {"labels": list(labels)})

    def remove_label(self, number: int, label: str) -> None:
        try:
            self._request(
                "DELETE",
                self._repo_path(f"/issues/{int(number)}/labels/{urllib.request.quote(label, safe='')}"),
            )
        except GitHubError as exc:
            if exc.status != 404:
                raise

    def close_issue(self, number: int) -> None:
        self._request("PATCH", self._repo_path(f"/issues/{int(number)}"), {"state": "closed"})

    # -- pull requests ----------------------------------------------------

    def get_pull(self, number: int) -> dict[str, Any]:
        _, _, data = self._request("GET", self._repo_path(f"/pulls/{int(number)}"))
        return data if isinstance(data, dict) else {}

    def list_pull_files(self, number: int) -> list[dict[str, Any]]:
        return self._paginate(self._repo_path(f"/pulls/{int(number)}/files"))

    def pulls_for_commit(self, sha: str) -> list[dict[str, Any]]:
        if not _SHA_RE.fullmatch(sha or ""):
            raise ValueError("expected a full commit sha")
        return self._paginate(self._repo_path(f"/commits/{sha}/pulls"))

    def merge_pull(self, number: int, head_sha: str, title: str) -> str:
        """Squash-merge only if the head is still exactly ``head_sha``."""
        if not _SHA_RE.fullmatch(head_sha or ""):
            raise ValueError("expected a full commit sha")
        _, _, data = self._request(
            "PUT",
            self._repo_path(f"/pulls/{int(number)}/merge"),
            {"sha": head_sha, "merge_method": "squash", "commit_title": title[:250]},
        )
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise GitHubError(0, "merge response carried no commit sha")
        return sha
