from __future__ import annotations

import argparse
import imaplib
import json
import os
import re
import time
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.mime.text import MIMEText
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from terminal_encoding import configure_terminal_encoding

HOST_ENV = "IMAP_HOST"
PORT_ENV = "IMAP_PORT"
USERNAME_ENV = "IMAP_USERNAME"
PASSWORD_ENV = "IMAP_PASSWORD"
DRAFTS_MAILBOX_ENV = "IMAP_DRAFTS_MAILBOX"
FROM_ENV = "IMAP_FROM"
DEFAULT_DRAFTS_MAILBOX = "Drafts"
MANAGED_HEADER = "X-Email-Triage-Managed"
SOURCE_REF_HEADER = "X-Email-Triage-Source-Ref"
MANAGED_VALUE = "true"
SOURCE_REF_FIELDS = ("source_ref", "source_thread_id", "thread_id", "source_message_id", "message_id")

configure_terminal_encoding()


def normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def build_draft_key(to: str, subject: str, body: str) -> str:
    return "\u241f".join((normalize_value(to), normalize_value(subject), normalize_value(body)))


def extract_source_ref(item: dict[str, Any]) -> str:
    for field_name in SOURCE_REF_FIELDS:
        candidate = str(item.get(field_name, "")).strip()
        if candidate:
            return candidate
    return ""


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


def decode_imap_line(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def parse_mailbox_from_list_line(line: str) -> str | None:
    # Common format: (<flags>) "<delimiter>" "<mailbox>"
    quoted = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', line)
    if quoted:
        return quoted.group(1).replace('\\"', '"')

    # Fallback for unquoted mailbox names.
    parts = line.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[1].strip()

    return None


def detect_drafts_mailbox(client: imaplib.IMAP4_SSL, fallback: str) -> str:
    status, data = client.list()
    if status != "OK" or not data:
        return fallback

    for raw_line in data:
        line = decode_imap_line(raw_line)
        if "\\drafts" not in line.lower():
            continue

        mailbox = parse_mailbox_from_list_line(line)
        if mailbox:
            return mailbox

    return fallback


def build_message(to: str, subject: str, body: str, from_address: str, source_ref: str) -> bytes:
    message = MIMEText(body, _charset="utf-8")
    message["To"] = to
    message["From"] = from_address
    message["Subject"] = subject
    message[MANAGED_HEADER] = MANAGED_VALUE
    if source_ref:
        message[SOURCE_REF_HEADER] = source_ref
    return message.as_bytes()


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""

    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


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


def uid_value(uid: bytes) -> int:
    try:
        return int(uid.decode("utf-8", errors="replace"))
    except ValueError:
        return 0


def remove_superseded_drafts(
    client: imaplib.IMAP4_SSL,
    drafts_mailbox: str,
    target_source_refs: set[str],
    target_fallback_keys: set[str],
) -> int:
    if not target_source_refs and not target_fallback_keys:
        return 0

    select_status, _ = client.select(mailbox=drafts_mailbox, readonly=False)
    if select_status != "OK":
        raise RuntimeError(f"Unable to select drafts mailbox for cleanup: {drafts_mailbox}.")

    search_status, search_data = client.uid("search", None, "ALL")
    if search_status != "OK":
        raise RuntimeError(f"Failed to list drafts mailbox for cleanup: {drafts_mailbox}.")

    raw_uids = search_data[0] if search_data else b""
    uids = [uid for uid in raw_uids.split() if uid]
    grouped: dict[str, list[tuple[int, bytes, str, str]]] = {}

    for uid in uids:
        fetch_status, fetch_data = client.uid("fetch", uid, "(RFC822)")
        if fetch_status != "OK":
            continue

        raw_email = extract_raw_email(fetch_data)
        if not raw_email:
            continue

        message = BytesParser(policy=policy.default).parsebytes(raw_email)
        managed_value = decode_header_value(message.get(MANAGED_HEADER, "")).strip().lower()
        if managed_value != MANAGED_VALUE:
            continue

        source_ref = decode_header_value(message.get(SOURCE_REF_HEADER, "")).strip()
        subject = decode_header_value(message.get("subject", ""))
        group_key = ""
        reason = ""
        if source_ref and source_ref in target_source_refs:
            group_key = f"source:{source_ref}"
            reason = source_ref
        else:
            to = decode_header_value(message.get("to", ""))
            body_text, body_html = extract_bodies(message)
            body = body_text or body_html
            content_key = build_draft_key(to=to, subject=subject, body=body)
            if content_key in target_fallback_keys:
                group_key = f"content:{content_key}"
                reason = subject or "(no subject)"

        if not group_key:
            continue

        grouped.setdefault(group_key, []).append((uid_value(uid), uid, subject, reason))

    removed = 0
    for entries in grouped.values():
        if len(entries) <= 1:
            continue

        # Keep the newest draft and remove older superseded versions.
        entries.sort(key=lambda item: (item[0], item[1]))
        for _, redundant_uid, subject, reason in entries[:-1]:
            store_status, _ = client.uid("store", redundant_uid, "+FLAGS.SILENT", r"(\Deleted)")
            if store_status == "OK":
                removed += 1
                uid_text = redundant_uid.decode("utf-8", errors="replace")
                print(f"Removed superseded draft UID {uid_text}: {subject} ({reason})")

    if removed:
        expunge_status, _ = client.expunge()
        if expunge_status != "OK":
            raise RuntimeError(f"Failed to expunge superseded drafts from mailbox: {drafts_mailbox}.")

    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Create IMAP drafts from reply-drafts.json.")
    parser.add_argument(
        "drafts_json",
        help=(
            "Path to JSON array with to/subject/body and optional source references "
            "(source_ref, source_thread_id/thread_id, source_message_id/message_id)."
        ),
    )
    parser.add_argument("--host", default=os.getenv(HOST_ENV, ""), help=f"IMAP host (default from {HOST_ENV}).")
    parser.add_argument("--port", type=int, default=None, help=f"IMAP SSL port (default from {PORT_ENV} or 993).")
    parser.add_argument("--username", default=os.getenv(USERNAME_ENV, ""), help=f"Mailbox login (default from {USERNAME_ENV}).")
    parser.add_argument("--password", default=os.getenv(PASSWORD_ENV, ""), help=f"Mailbox password (default from {PASSWORD_ENV}).")
    parser.add_argument(
        "--drafts-mailbox",
        default=os.getenv(DRAFTS_MAILBOX_ENV, ""),
        help=f"Drafts mailbox path (default from {DRAFTS_MAILBOX_ENV} or auto-detect).",
    )
    parser.add_argument(
        "--from-address",
        default=os.getenv(FROM_ENV, ""),
        help=f"From address (default from {FROM_ENV} or IMAP username).",
    )
    args = parser.parse_args()

    drafts_path = Path(args.drafts_json)
    items = json.loads(drafts_path.read_text(encoding="utf-8"))

    if not isinstance(items, list):
        raise ValueError("Draft JSON must contain a top-level array.")

    host = require_value(HOST_ENV, args.host)
    username = require_value(USERNAME_ENV, args.username)
    password = require_value(PASSWORD_ENV, args.password)
    port = args.port if args.port is not None else env_int(PORT_ENV, 993)
    from_address = args.from_address.strip() or os.getenv(FROM_ENV, "").strip() or username

    with imaplib.IMAP4_SSL(host=host, port=port) as client:
        login_status, _ = client.login(username, password)
        if login_status != "OK":
            raise RuntimeError("IMAP login failed.")

        configured_drafts_mailbox = args.drafts_mailbox.strip() or os.getenv(DRAFTS_MAILBOX_ENV, "").strip()
        drafts_mailbox = configured_drafts_mailbox or detect_drafts_mailbox(client, DEFAULT_DRAFTS_MAILBOX)

        created = 0
        target_source_refs: set[str] = set()
        target_fallback_keys: set[str] = set()
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Draft item #{idx} must be a JSON object.")

            to = str(item.get("to", "")).strip()
            subject = str(item.get("subject", "")).strip()
            body = str(item.get("body", "")).strip()

            if not to or not subject or not body:
                raise ValueError(f"Draft item #{idx} is missing to, subject, or body.")

            source_ref = extract_source_ref(item)
            if source_ref:
                target_source_refs.add(source_ref)
            else:
                target_fallback_keys.add(build_draft_key(to=to, subject=subject, body=body))

            raw_message = build_message(
                to=to,
                subject=subject,
                body=body,
                from_address=from_address,
                source_ref=source_ref,
            )
            append_status, _ = client.append(
                drafts_mailbox,
                r"(\Draft)",
                imaplib.Time2Internaldate(time.time()),
                raw_message,
            )
            if append_status != "OK":
                raise RuntimeError(f"Failed to append draft #{idx} to mailbox '{drafts_mailbox}'.")

            created += 1
            print(f"Created draft {created}: {subject}")

        removed = remove_superseded_drafts(
            client=client,
            drafts_mailbox=drafts_mailbox,
            target_source_refs=target_source_refs,
            target_fallback_keys=target_fallback_keys,
        )

    print(f"Created {created} drafts in IMAP mailbox: {drafts_mailbox}")
    print(f"Removed {removed} superseded draft(s).")


if __name__ == "__main__":
    main()
