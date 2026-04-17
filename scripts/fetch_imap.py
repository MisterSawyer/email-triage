from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from terminal_encoding import configure_terminal_encoding, safe_print

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "emails.json"

HOST_ENV = "IMAP_HOST"
PORT_ENV = "IMAP_PORT"
USERNAME_ENV = "IMAP_USERNAME"
PASSWORD_ENV = "IMAP_PASSWORD"
MAILBOX_ENV = "IMAP_MAILBOX"

configure_terminal_encoding()


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""

    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def decode_part_text(part: Message) -> str:
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            charset = part.get_content_charset() or "utf-8"
            return content.decode(charset, errors="replace")
        return str(content)
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")


def extract_bodies(message: Message) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    candidates = message.walk() if message.is_multipart() else [message]
    for part in candidates:
        if part.get_content_maintype() == "multipart":
            continue

        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue

        content_type = part.get_content_type().lower()
        text = decode_part_text(part).strip()
        if not text:
            continue

        if content_type == "text/plain":
            plain_parts.append(text)
        elif content_type == "text/html":
            html_parts.append(text)

    plain_text = "\n\n".join(plain_parts).strip()
    html_text = "\n\n".join(html_parts).strip()
    return plain_text, html_text


def extract_raw_email(fetch_data: list[Any]) -> bytes:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]

    return b""


def to_record(uid: bytes, raw_email: bytes) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw_email)
    body_text, body_html = extract_bodies(message)

    snippet_source = body_text or body_html or decode_header_value(message.get("subject", ""))
    snippet = normalize_whitespace(snippet_source)[:200]

    message_id = decode_header_value(message.get("message-id", "")).strip()
    uid_text = uid.decode("utf-8", errors="replace")
    if not message_id:
        message_id = f"imap-uid-{uid_text}"

    return {
        "message_id": message_id,
        "internet_message_id": decode_header_value(message.get("message-id", "")).strip(),
        "in_reply_to": decode_header_value(message.get("in-reply-to", "")).strip(),
        "references": decode_header_value(message.get("references", "")).strip(),
        "thread_id": "",
        "label_ids": [],
        "from": decode_header_value(message.get("from", "")),
        "to": decode_header_value(message.get("to", "")),
        "cc": decode_header_value(message.get("cc", "")),
        "subject": decode_header_value(message.get("subject", "")),
        "received_at": decode_header_value(message.get("date", "")),
        "snippet": snippet,
        "body_text": body_text,
        "body_html": body_html,
    }


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def require_value(label: str, value: str) -> str:
    clean = value.strip()
    if not clean:
        raise EnvironmentError(f"Missing required setting: {label}.")
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch mailbox messages over IMAP into a local JSON file.")
    parser.add_argument("--host", default=os.getenv(HOST_ENV, ""), help=f"IMAP host (default from {HOST_ENV}).")
    parser.add_argument("--port", type=int, default=None, help=f"IMAP SSL port (default from {PORT_ENV} or 993).")
    parser.add_argument("--username", default=os.getenv(USERNAME_ENV, ""), help=f"Mailbox login (default from {USERNAME_ENV}).")
    parser.add_argument("--password", default=os.getenv(PASSWORD_ENV, ""), help=f"Mailbox password (default from {PASSWORD_ENV}).")
    parser.add_argument("--mailbox", default=os.getenv(MAILBOX_ENV, "INBOX"), help=f"Mailbox folder (default from {MAILBOX_ENV} or INBOX).")
    parser.add_argument(
        "--search",
        nargs="+",
        default=["ALL"],
        help="IMAP SEARCH tokens, for example: --search SINCE 01-Jan-2026.",
    )
    parser.add_argument("--limit", type=int, default=500, help="Maximum number of messages to fetch.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    args = parser.parse_args()

    if args.limit <= 0:
        raise ValueError("--limit must be greater than 0.")

    host = require_value(HOST_ENV, args.host)
    username = require_value(USERNAME_ENV, args.username)
    password = require_value(PASSWORD_ENV, args.password)
    port = args.port if args.port is not None else env_int(PORT_ENV, 993)
    mailbox = args.mailbox.strip() or "INBOX"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    collected: list[dict[str, Any]] = []

    with imaplib.IMAP4_SSL(host=host, port=port) as client:
        login_status, _ = client.login(username, password)
        if login_status != "OK":
            raise RuntimeError("IMAP login failed.")

        select_status, _ = client.select(mailbox=mailbox, readonly=True)
        if select_status != "OK":
            raise RuntimeError(f"Unable to select mailbox: {mailbox}.")

        search_status, search_data = client.uid("search", None, *args.search)
        if search_status != "OK":
            raise RuntimeError(f"IMAP search failed for criteria: {' '.join(args.search)}")

        raw_uids = search_data[0] if search_data else b""
        message_uids = [uid for uid in raw_uids.split() if uid]

        for uid in reversed(message_uids[-args.limit:]):
            fetch_status, fetch_data = client.uid("fetch", uid, "(RFC822)")
            if fetch_status != "OK":
                continue

            raw_email = extract_raw_email(fetch_data)
            if not raw_email:
                continue

            collected.append(to_record(uid=uid, raw_email=raw_email))

    output_path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"Saved {len(collected)} emails to {output_path}")


if __name__ == "__main__":
    main()
