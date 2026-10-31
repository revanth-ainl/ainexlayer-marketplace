"""Content hashes published in the feed.

``content_sha256`` covers a whole package folder; ``raw_sha256`` (a skill's
``SKILL.md``) and each bundled skill's ``sha256`` are :func:`file_sha256` of
the exact bytes a raw GitHub URL serves.

Recipe for ``content_sha256``: take every file in the folder except the ones
:func:`security.is_ignored` skips (``.DS_Store``), sorted by POSIX relative
path; write one line per file, ``<sha256 hex of its raw bytes>  <relpath>\\n``;
the SHA-256 of that manifest, encoded as UTF-8, is the package hash. The
same recipe reproduces it with standard tools::

    cd skills/<name> && find . -type f ! -name .DS_Store | sed 's|^\\./||' | LC_ALL=C sort \\
      | while read -r f; do printf '%s  %s\\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done \\
      | shasum -a 256
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import security


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package_files(folder: Path) -> list[str]:
    """POSIX relative paths of every file the hash covers, in hash order."""
    folder = Path(folder)
    return sorted(
        p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and not security.is_ignored(p)
    )


def content_sha256(folder: Path) -> str:
    folder = Path(folder)
    manifest = "".join(f"{file_sha256(folder / rel)}  {rel}\n" for rel in package_files(folder))
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()
