import atexit
import os
import sys
from pathlib import Path
from typing import TextIO


class _Tee:
    def __init__(self, original: TextIO, mirror: TextIO):
        self._original = original
        self._mirror = mirror

    def write(self, data):
        self._original.write(data)
        self._mirror.write(data)
        return len(data)

    def flush(self):
        self._original.flush()
        self._mirror.flush()

    def isatty(self):
        return self._original.isatty()

    def fileno(self):
        return self._original.fileno()


_log_handle: TextIO | None = None


def _close_log_handle():
    global _log_handle
    if _log_handle is not None:
        try:
            _log_handle.flush()
        finally:
            _log_handle.close()
            _log_handle = None


def activate_file_logging() -> str | None:
    global _log_handle

    raw_path = os.getenv("GATEWAY_LOG_FILE", "").strip()
    if not raw_path:
        return None
    if _log_handle is not None:
        return raw_path

    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_handle = path.open("a", encoding="utf-8", buffering=1)

    sys.stdout = _Tee(sys.stdout, _log_handle)
    sys.stderr = _Tee(sys.stderr, _log_handle)
    atexit.register(_close_log_handle)
    return raw_path
