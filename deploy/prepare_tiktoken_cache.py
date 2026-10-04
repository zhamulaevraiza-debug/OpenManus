"""Pre-populate a tiktoken cache directory while building the Docker image.

tiktoken downloads its BPE files on first use, which fails on servers without access to
the download host. This script copies the encodings bundled with litellm (a crawl4ai
dependency) into the target directory and then loads every encoding the application
uses, so any encoding that is not bundled is downloaded once at build time.

Usage: python prepare_tiktoken_cache.py <cache-dir>
"""

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional


REQUIRED_ENCODINGS = ("cl100k_base", "o200k_base")
OPTIONAL_ENCODINGS = ("p50k_base",)

# tiktoken names cache files after the SHA-1 of the download URL.
CACHE_FILE_NAME = re.compile(r"^[0-9a-f]{40}$")


def bundled_encodings_dir() -> Optional[Path]:
    """Directory of the tiktoken files shipped inside the litellm package."""
    spec = importlib.util.find_spec("litellm")
    for location in (spec.submodule_search_locations or []) if spec else []:
        path = Path(location) / "litellm_core_utils" / "tokenizers"
        if path.is_dir():
            return path
    return None


def copy_bundled(target: Path) -> int:
    """Copy litellm's bundled encodings into ``target``; returns the file count."""
    source = bundled_encodings_dir()
    if source is None:
        return 0
    copied = 0
    for path in source.iterdir():
        if path.is_file() and CACHE_FILE_NAME.match(path.name):
            shutil.copyfile(path, target / path.name)
            copied += 1
    return copied


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    target = Path(argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    copied = copy_bundled(target)

    os.environ["TIKTOKEN_CACHE_DIR"] = str(target)
    import tiktoken

    for name in REQUIRED_ENCODINGS:
        tiktoken.get_encoding(name)
    for name in OPTIONAL_ENCODINGS:
        try:
            tiktoken.get_encoding(name)
        except Exception as e:  # network errors surface as many exception types
            print(
                f"warning: optional encoding {name} unavailable: {e}", file=sys.stderr
            )

    target.chmod(0o755)
    for path in target.iterdir():
        path.chmod(0o644)
    print(
        f"tiktoken cache ready in {target}: {copied} bundled file(s), "
        f"{len(list(target.iterdir()))} total"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
