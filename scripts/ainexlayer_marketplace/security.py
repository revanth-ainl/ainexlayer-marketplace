"""Checks on raw text and on package folders: credentials, paths, sizes.

These run before anything reads a package's content as data, so a folder
that holds a symlink, a ``..`` path or an oversized file is refused before
its JSON is parsed.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Sequence

from .rules import (
    AGENT_SECRET_PATTERNS,
    GENERIC_SECRET_RE,
    MAX_PACKAGE_FILE_BYTES,
    SECRET_PATTERNS,
    FieldError,
    is_valid_name,
    warning,
)

#: File types a package folder may hold. A package is manifests and Markdown;
#: anything else is code or data the app never reads.
ALLOWED_SUFFIXES = frozenset({".json", ".md"})


def scan_text(text: str, where: str, *, strict: bool = False) -> List[FieldError]:
    """Credentials in ``text``.

    The app's eight token families are always an error. The generic
    ``password = ...`` shape is an error when ``strict`` (agent templates,
    as the app refuses it there) and a warning elsewhere, because Markdown
    instructions legitimately say things like ``token: <your token>``.
    """
    patterns = AGENT_SECRET_PATTERNS if strict else SECRET_PATTERNS
    for pattern in patterns:
        if pattern.search(text):
            return [
                FieldError(
                    f"{where} appears to contain a credential; remove it and use a $plugin_… placeholder",
                    where,
                )
            ]
    if not strict and GENERIC_SECRET_RE.search(text):
        return [warning(f"{where} has something shaped like 'key = value'; make sure it is not a real credential", where)]
    return []


def is_safe_relpath(rel: str) -> bool:
    """A relative POSIX path with no ``..``, no absolute root, no backslash
    and no empty or hidden segment."""
    if not rel or "\\" in rel or "\x00" in rel or rel.startswith("/"):
        return False
    parts = PurePosixPath(rel).parts
    if not parts:
        return False
    return all(part not in ("", ".", "..") and not part.startswith(".") for part in parts)


def safe_join(root: Path, rel: str) -> Optional[Path]:
    """``root / rel`` when ``rel`` is safe and stays inside ``root``, else ``None``.

    For anything that writes package files from submitted data.
    """
    if not is_safe_relpath(rel):
        return None
    root_resolved = root.resolve()
    target = (root_resolved / rel).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target


def _walk(directory: Path) -> Iterable[Path]:
    # os.walk with followlinks=False never descends through a symlinked
    # directory; the link itself is still reported and refused below.
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in sorted(dirnames):
            yield base / name
        for name in sorted(filenames):
            yield base / name


def check_tree(
    directory: Path,
    *,
    where: Optional[str] = None,
    allowed_suffixes: Sequence[str] = tuple(ALLOWED_SUFFIXES),
    max_bytes: int = MAX_PACKAGE_FILE_BYTES,
) -> List[FieldError]:
    """Everything wrong with the files of one package folder.

    Refuses symlinks (a link to ``~/.ssh`` would be read as package
    content), hidden files, unsafe names, file types outside
    ``allowed_suffixes``, files over ``max_bytes`` and text that is not
    UTF-8. Content is scanned for credentials.
    """
    label = where or directory.name
    errors: List[FieldError] = []
    if directory.is_symlink():
        return [FieldError(f"{label} is a symlink; packages must be plain folders", label)]
    if not directory.is_dir():
        return [FieldError(f"{label} is not a folder", label)]
    for path in _walk(directory):
        rel = path.relative_to(directory).as_posix()
        shown = f"{label}/{rel}"
        if path.is_symlink():
            errors.append(FieldError(f"{shown} is a symlink; symlinks are not allowed in a package", shown))
            continue
        if not is_safe_relpath(rel):
            errors.append(FieldError(f"{shown}: hidden or unsafe path", shown))
            continue
        if path.is_dir():
            if not is_valid_name(path.name):
                errors.append(FieldError(f"{shown}: folder name must follow the package name rules", shown))
            continue
        if not path.is_file():
            errors.append(FieldError(f"{shown} is not a regular file", shown))
            continue
        if path.suffix.lower() not in allowed_suffixes:
            errors.append(
                FieldError(f"{shown}: only {', '.join(sorted(allowed_suffixes))} files may be published", shown)
            )
            continue
        size = path.stat().st_size
        if size > max_bytes:
            errors.append(FieldError(f"{shown} is {size} bytes; the limit is {max_bytes}", shown))
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            errors.append(FieldError(f"{shown} is not UTF-8 text", shown))
            continue
        errors.extend(scan_text(text, shown))
    return errors
