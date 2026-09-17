import logging
import os
from pathlib import Path

_INITIALIZED = False
_HARNESS_HOME_DIR = ".proto_harness"
_LOG_FILE_RELATIVE = Path("logs") / "proto.log"


def _default_log_file() -> Path:
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        if (directory / _HARNESS_HOME_DIR).is_dir():
            return directory / _HARNESS_HOME_DIR / _LOG_FILE_RELATIVE
    return Path(_HARNESS_HOME_DIR) / _LOG_FILE_RELATIVE


def _resolve_log_handler() -> logging.Handler:
    configured = os.environ.get("PROTO_LOG_FILE")
    if configured == "":
        return logging.NullHandler()

    path = Path(configured) if configured else _default_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def init_logger(level: str | None = None) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    resolved = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=resolved,
        handlers=[_resolve_log_handler()],
        force=True,
    )
    _INITIALIZED = True
