"""Tests for the Windows file:// compat shim (path reconstruction + allowlist).

These exercise ``_read_local_file`` directly (platform-independent), covering
the actual Windows-quoted URL form that triggered the bug, the POSIX-style
drive form, and the security-relevant allowlist / non-absolute rejections.
"""

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import pytest

from app import genblaze_compat
from genblaze_core.storage import transfer as _transfer


def _make_temp_file(content: bytes) -> Path:
    root = Path(tempfile.gettempdir()) / "brandforge-test"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "asset.png"
    path.write_bytes(content)
    return path


@pytest.mark.unit
def test_reads_windows_quoted_file_url():
    path = _make_temp_file(b"abc")
    # The actual buggy form genblaze emits on Windows: file://{quote(winpath)}.
    url = "file://" + quote(str(path))
    data, _ = genblaze_compat._read_local_file(url)
    assert data == b"abc"


@pytest.mark.unit
def test_reads_posix_style_drive_url():
    path = _make_temp_file(b"xyz")
    # file:///C:/Users/... — leading slash before the drive letter.
    url = "file:///" + str(path).replace("\\", "/")
    data, _ = genblaze_compat._read_local_file(url)
    assert data == b"xyz"


@pytest.mark.unit
def test_rejects_path_outside_allowed_roots():
    url = "file://" + quote("C:\\Windows\\System32\\drivers\\etc\\hosts")
    with pytest.raises(_transfer.StorageError):
        genblaze_compat._read_local_file(url)


@pytest.mark.unit
def test_rejects_non_absolute_url():
    # netloc without a leading slash would otherwise resolve against the CWD.
    with pytest.raises(_transfer.StorageError):
        genblaze_compat._read_local_file("file://server/share/secret.txt")


@pytest.mark.unit
def test_apply_is_idempotent():
    if os.name != "nt":
        pytest.skip("shim only patches on Windows")
    genblaze_compat.apply()
    first = _transfer._read_local_file
    genblaze_compat.apply()
    assert _transfer._read_local_file is first
    assert _transfer._read_local_file is genblaze_compat._read_local_file
