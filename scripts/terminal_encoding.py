from __future__ import annotations

import builtins
import codecs
import os
import sys
from typing import TextIO


def _normalize_error_mode(errors: str, fallback: str = "replace") -> str:
    candidate = (errors or "").strip()
    fallback_candidate = (fallback or "").strip() or "replace"

    try:
        if candidate:
            codecs.lookup_error(candidate)
            return candidate
    except LookupError:
        pass

    try:
        codecs.lookup_error(fallback_candidate)
        return fallback_candidate
    except LookupError:
        return "replace"


def _resolve_encoding_settings(default_encoding: str, default_errors: str) -> tuple[str, str]:
    requested = os.getenv("PYTHONENCODING", "").strip() or os.getenv("PYTHONIOENCODING", "").strip()
    errors = _normalize_error_mode(os.getenv("PYTHONIOERRORS", "").strip() or default_errors)

    if requested and ":" in requested:
        requested_encoding, requested_errors = requested.split(":", maxsplit=1)
        encoding = requested_encoding.strip() or default_encoding
        if requested_errors.strip():
            errors = _normalize_error_mode(requested_errors.strip(), fallback=errors)
        return encoding, errors

    return requested or default_encoding, errors


def _reconfigure_stream(stream: TextIO, encoding: str, errors: str) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        normalized_errors = _normalize_error_mode(errors)
        try:
            reconfigure(encoding=encoding, errors=normalized_errors)
        except LookupError:
            reconfigure(encoding="utf-8", errors="replace")


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def sanitize_text(
    value: object,
    *,
    encoding: str | None = None,
    errors: str = "replace",
    ascii_only: bool = False,
) -> str:
    text = str(value)
    if ascii_only:
        return text.encode("ascii", errors="ignore").decode("ascii")

    target_encoding = encoding or "utf-8"
    normalized_errors = _normalize_error_mode(errors)
    try:
        return text.encode(target_encoding, errors=normalized_errors).decode(target_encoding, errors=normalized_errors)
    except LookupError:
        return text.encode("utf-8", errors=normalized_errors).decode("utf-8", errors=normalized_errors)


def safe_print(*values: object, sep: str = " ", end: str = "\n", file: TextIO | None = None, flush: bool = False) -> None:
    stream = file or sys.stdout
    stream_encoding = getattr(stream, "encoding", None) or "utf-8"
    output_errors = _normalize_error_mode(os.getenv("EMAIL_TRIAGE_PRINT_ERRORS", "replace").strip() or "replace")
    ascii_only = _is_true(os.getenv("EMAIL_TRIAGE_PRINT_ASCII_ONLY", ""))
    rendered = sep.join(
        sanitize_text(
            value,
            encoding=stream_encoding,
            errors=output_errors,
            ascii_only=ascii_only,
        )
        for value in values
    )
    builtins.print(rendered, sep="", end=end, file=stream, flush=flush)


def configure_terminal_encoding(default: str = "utf-8", default_errors: str = "replace") -> None:
    encoding, errors = _resolve_encoding_settings(default_encoding=default, default_errors=default_errors)
    _reconfigure_stream(sys.stdout, encoding=encoding, errors=errors)
    _reconfigure_stream(sys.stderr, encoding=encoding, errors=errors)
