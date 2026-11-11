"""Tolerant file readers and crash-safe writers.

Collector runs are cheap to repeat but expensive to lose: a half-written
JSONL file silently truncates the evidence base. Every write therefore goes
through a temporary file in the same directory and is moved into place with
``os.replace``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* without ever exposing a partial file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="\n") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_json(path: Path, value: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=indent) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def read_json(path: Path) -> tuple[Any, list[str]]:
    """Return ``(value, errors)`` instead of raising on malformed input."""

    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, ["{}: {}".format(Path(path).name, error)]


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    errors.append("{} line {}: {}".format(Path(path).name, line_number, error))
                    continue
                if not isinstance(value, dict):
                    errors.append("{} line {}: expected a JSON object".format(Path(path).name, line_number))
                    continue
                rows.append(value)
    except OSError as error:
        errors.append("{}: {}".format(Path(path).name, error))
    return rows, errors


def read_jsonl_directory(directory: Path) -> tuple[list[dict], list[str], list[Path]]:
    """Read every ``*.jsonl`` file in *directory* (not recursive)."""

    directory = Path(directory)
    rows: list[dict] = []
    errors: list[str] = []
    files = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    for path in files:
        file_rows, file_errors = read_jsonl(path)
        for row in file_rows:
            row.setdefault("_discovery_file", path.name)
        rows.extend(file_rows)
        errors.extend(file_errors)
    return rows, errors, files
