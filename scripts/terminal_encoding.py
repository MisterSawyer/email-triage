from __future__ import annotations

import os
import sys
from typing import TextIO


def _resolve_encoding(default: str) -> str:
    requested = os.getenv("PYTHONENCODING", "").strip()
    if not requested:
        requested = os.getenv("PYTHONIOENCODING", "").strip()

    if ":" in requested:
        requested = requested.split(":", maxsplit=1)[0].strip()

    return requested or default


def _reconfigure_stream(stream: TextIO, encoding: str) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding=encoding, errors="replace")


def configure_terminal_encoding(default: str = "utf-8") -> None:
    encoding = _resolve_encoding(default=default)
    _reconfigure_stream(sys.stdout, encoding=encoding)
    _reconfigure_stream(sys.stderr, encoding=encoding)
