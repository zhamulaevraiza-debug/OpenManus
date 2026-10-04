"""Token counting that keeps working on offline servers.

tiktoken downloads its BPE files on first use. To work without network access we point
``TIKTOKEN_CACHE_DIR`` at the encodings bundled with litellm (a dependency of crawl4ai)
when no cache directory is configured. If an encoding still cannot be loaded, or
encoding a text fails, token counts fall back to a ``len(text) // 4`` approximation.
"""

import importlib.util
import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.logger import logger


def _configure_tiktoken_cache() -> None:
    """Use litellm's bundled tiktoken encodings unless a cache dir is configured."""
    if os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR"):
        return
    try:
        spec = importlib.util.find_spec("litellm")
    except (ImportError, ValueError):
        return
    for location in (spec.submodule_search_locations or []) if spec else []:
        bundled = Path(location) / "litellm_core_utils" / "tokenizers"
        if bundled.is_dir():
            os.environ["TIKTOKEN_CACHE_DIR"] = str(bundled)
            return


_configure_tiktoken_cache()

import tiktoken  # noqa: E402  (must be imported after the cache dir is configured)


DEFAULT_ENCODING = "cl100k_base"
CHARS_PER_TOKEN = 4

_warned = False


def _warn_once(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning(f"{message}; using an approximate token counter")


class Tokenizer(Protocol):
    """Minimal interface used for token accounting."""

    name: str

    def count(self, text: str) -> int:
        """Return the number of tokens in ``text``."""


class ApproximateTokenizer:
    """Fallback tokenizer estimating one token per four characters."""

    name = "approximate"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // CHARS_PER_TOKEN)


class TiktokenTokenizer:
    """tiktoken-backed tokenizer that degrades to the approximation on errors."""

    def __init__(self, encoding: "tiktoken.Encoding"):
        self._encoding = encoding
        self.name = encoding.name

    def count(self, text: str) -> int:
        if not text:
            return 0
        try:
            return len(self._encoding.encode(text, disallowed_special=()))
        except Exception as e:
            _warn_once(f"tiktoken failed to encode text ({e})")
            return ApproximateTokenizer().count(text)


@lru_cache(maxsize=32)
def get_tokenizer(model: str) -> Tokenizer:
    """Return a (cached) tokenizer for ``model``; never raises."""
    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding(DEFAULT_ENCODING)
        return TiktokenTokenizer(encoding)
    except Exception as e:
        _warn_once(f"Cannot load a tiktoken encoding for '{model}' ({e})")
        return ApproximateTokenizer()
