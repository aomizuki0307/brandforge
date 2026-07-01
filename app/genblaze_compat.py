"""Windows compatibility shim for Genblaze local (``file://``) asset transfer.

Genblaze builds local asset URLs as ``file://{quote(str(path))}`` (see
``genblaze_openai/dalle.py``, ``providers/compositor.py``, ``providers/transform.py``).
On Windows that quotes the drive colon and backslashes, producing
``file://C%3A%5CUsers%5C...png`` — a URL whose *entire* path lands in the netloc
(there is no ``/`` separator). The sink's ``_read_local_file`` then reads an
empty ``parsed.path``, resolves it to the current working directory, and rejects
the upload as "outside allowed directories".

This shim replaces that consumer with a version that reconstructs the real path
from ``netloc + path`` (and strips a leading slash before a drive letter), so
image/video assets upload to B2 on Windows. It is idempotent and a no-op on
POSIX, where the original URLs already parse correctly.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from genblaze_core.storage import transfer as _transfer

_PATCHED_FLAG = "_brandforge_win_file_url_patched"


def _is_absolute(raw: str) -> bool:
    """True for a Windows drive path, a UNC path, or a POSIX absolute path."""
    if raw.startswith(("/", "\\")):
        return True
    return len(raw) >= 2 and raw[1] == ":"


def _read_local_file(
    url: str, *, extra_roots: list[Path] | None = None
) -> tuple[bytes, str | None]:
    """Windows-safe re-implementation of ``transfer._read_local_file``."""
    parsed = urlparse(url)
    raw = unquote((parsed.netloc or "") + (parsed.path or ""))
    # "/C:/x" -> "C:/x" (POSIX-style file:///C:/... form)
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    # Reject non-absolute paths rather than let Path.resolve() fall back to CWD
    # (the original resolved from parsed.path only and never had this ambiguity).
    if not _is_absolute(raw):
        raise _transfer.StorageError(f"Malformed file:// URL, not absolute: {url!r}")
    resolved = Path(raw).resolve()

    allowed = list(_transfer.ALLOWED_FILE_ROOTS)
    if extra_roots:
        allowed.extend(r.resolve() for r in extra_roots)

    if not any(resolved.is_relative_to(root) for root in allowed):
        raise _transfer.StorageError(
            f"Access denied: local file path {resolved} is outside allowed directories. "
            f"Files must be under temp or output_dir."
        )

    try:
        data = resolved.read_bytes()
    except Exception as exc:  # pragma: no cover - passthrough of read errors
        raise _transfer.StorageError(f"Failed to read local file {raw}: {exc}") from exc
    content_type, _ = mimetypes.guess_type(str(resolved))
    return data, content_type


def apply() -> None:
    """Install the shim once. No-op on non-Windows platforms.

    Guards on the target existing so that if a future genblaze release renames
    or removes the private ``_read_local_file``, we skip patching instead of
    silently creating a dead attribute (which would let the Windows bug return
    unnoticed).
    """
    if os.name != "nt":
        return
    if getattr(_transfer, _PATCHED_FLAG, False):
        return
    if not hasattr(_transfer, "_read_local_file"):  # pragma: no cover - upstream drift guard
        return
    _transfer._read_local_file = _read_local_file
    setattr(_transfer, _PATCHED_FLAG, True)
