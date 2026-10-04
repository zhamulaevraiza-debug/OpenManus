"""Process-wide loguru configuration.

Environment variables:
    OPENMANUS_LOG_LEVEL       console level (default INFO)
    OPENMANUS_LOG_FILE_LEVEL  file level (default DEBUG)
    OPENMANUS_LOG_DIR         directory for the rotating log file (default <repo>/logs;
                              an empty value disables file logging)
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

from loguru import logger as _logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
LOG_ROTATION = "20 MB"
LOG_RETENTION = 10  # number of rotated files to keep

# Handlers added by this module; only these are replaced on reconfiguration so that
# sinks installed by other components (e.g. the web server) survive.
_handler_ids: List[int] = []


def _log_dir() -> Optional[Path]:
    value = os.environ.get("OPENMANUS_LOG_DIR")
    if value is None:
        return DEFAULT_LOG_DIR
    value = value.strip()
    return Path(value).expanduser() if value else None


def _level(requested: Optional[str], env_var: str, default: str) -> str:
    level = (requested or os.environ.get(env_var) or default).strip().upper()
    try:
        _logger.level(level)
    except ValueError:
        return default
    return level


def define_log_level(
    print_level: Optional[str] = None,
    logfile_level: Optional[str] = None,
    name: Optional[str] = None,
):
    """(Re)configure the console and rotating file sinks owned by this module.

    Args:
        print_level: Console level; defaults to ``OPENMANUS_LOG_LEVEL`` or INFO.
        logfile_level: File level; defaults to ``OPENMANUS_LOG_FILE_LEVEL`` or DEBUG.
        name: Log file name prefix (``<name>.log``); defaults to ``openmanus``.
    """
    print_level = _level(print_level, "OPENMANUS_LOG_LEVEL", "INFO")
    logfile_level = _level(logfile_level, "OPENMANUS_LOG_FILE_LEVEL", "DEBUG")

    for handler_id in _handler_ids:
        try:
            _logger.remove(handler_id)
        except ValueError:
            pass
    _handler_ids.clear()
    try:
        _logger.remove(0)  # loguru's default stderr handler
    except ValueError:
        pass

    _handler_ids.append(_logger.add(sys.stderr, level=print_level))

    log_dir = _log_dir()
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            _handler_ids.append(
                _logger.add(
                    log_dir / f"{name or 'openmanus'}.log",
                    level=logfile_level,
                    rotation=LOG_ROTATION,
                    retention=LOG_RETENTION,
                    enqueue=True,
                    encoding="utf-8",
                )
            )
        except OSError as e:
            _logger.warning(f"File logging disabled, cannot write to {log_dir}: {e}")
    return _logger


logger = define_log_level()
