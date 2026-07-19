from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


_SCP_STYLE_URI = re.compile(r"^[A-Za-z0-9_.-]+@[^:]+:.+")


def resolve_local_uri(base_root: Path, uri: str) -> Path | None:
    value = uri.strip()
    if not value or value.startswith("pkg://"):
        return None
    if value.startswith("file://"):
        parsed = urlparse(value)
        return Path(unquote(parsed.path)).expanduser().resolve()
    if "://" in value or _SCP_STYLE_URI.match(value):
        return None

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base_root / candidate
    return candidate.resolve()


def resolve_structured_ref_path(base_root: Path, ref: Any) -> Path | None:
    if not isinstance(ref, dict):
        return None
    return resolve_local_uri(base_root, str(ref.get("uri", "")))


def is_offline_fixture_git_ref(ref: Any) -> bool:
    if not isinstance(ref, dict):
        return False
    if ref.get("kind") != "git" or ref.get("pinType") != "commit":
        return False
    parsed = urlparse(str(ref.get("uri", "")).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.hostname.endswith(
        ".invalid"
    )


def path_present(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return True
    try:
        return any(path.iterdir())
    except OSError:
        return True
