"""deploy/prepare_tiktoken_cache.py: offline tiktoken cache baked into the image."""

import hashlib
import os
import subprocess
import sys

import prepare_tiktoken_cache
import pytest


ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/{}.tiktoken"


def _cache_file_name(encoding: str) -> str:
    """tiktoken's cache key: SHA-1 of the download URL."""
    return hashlib.sha1(ENCODING_URL.format(encoding).encode()).hexdigest()


@pytest.mark.skipif(
    prepare_tiktoken_cache.bundled_encodings_dir() is None,
    reason="litellm with bundled encodings is not installed",
)
def test_bundled_required_encodings_are_copied(tmp_path):
    copied = prepare_tiktoken_cache.copy_bundled(tmp_path)

    names = {path.name for path in tmp_path.iterdir()}
    assert copied == len(names) >= 2
    for encoding in prepare_tiktoken_cache.REQUIRED_ENCODINGS:
        assert _cache_file_name(encoding) in names
    assert all(prepare_tiktoken_cache.CACHE_FILE_NAME.match(name) for name in names)


def test_script_prepares_a_readable_cache(tmp_path):
    target = tmp_path / "tiktoken"
    env = {k: v for k, v in os.environ.items() if k != "TIKTOKEN_CACHE_DIR"}

    result = subprocess.run(
        [sys.executable, prepare_tiktoken_cache.__file__, str(target)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "tiktoken cache ready" in result.stdout
    assert target.stat().st_mode & 0o777 == 0o755
    for encoding in prepare_tiktoken_cache.REQUIRED_ENCODINGS:
        cached = target / _cache_file_name(encoding)
        assert cached.stat().st_mode & 0o777 == 0o644


def test_usage_error_without_target(capsys):
    assert prepare_tiktoken_cache.main(["prepare_tiktoken_cache.py"]) == 2
    assert "Usage:" in capsys.readouterr().err
