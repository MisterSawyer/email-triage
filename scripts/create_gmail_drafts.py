from __future__ import annotations

import argparse
import base64
import json
import os
import re
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from terminal_encoding import configure_terminal_encoding

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / "token-compose.json"
CLIENT_CONFIG_ENV = "GMAIL_OAUTH_CLIENT_CONFIG_JSON"
CLIENT_ID_ENV = "GMAIL_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "GMAIL_OAUTH_CLIENT_SECRET"
PROJECT_ID_ENV = "GMAIL_OAUTH_PROJECT_ID"
MANAGED_HEADER = "X-Email-Triage-Managed"
SOURCE_REF_HEADER = "X-Email-Triage-Source-Ref"
MANAGED_VALUE = "true"
SOURCE_REF_FIELDS = (
    "source_ref",
    "source_thread_id",
    "thread_id",
    "source_message_id",
    "internet_message_id",
    "message_id",
)
SOURCE_THREAD_FIELDS = ("source_thread_id", "thread_id")
SOURCE_MESSAGE_FIELDS = ("source_message_id", "internet_message_id", "message_id")
SOURCE_REFERENCES_FIELDS = ("source_references", "references")

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


def extract_thread_id(item: dict[str, Any]) -> str:
    for field_name in SOURCE_THREAD_FIELDS:
        candidate = str(item.get(field_name, "")).strip()
        if candidate:
            return candidate
    return ""


def normalize_message_id(value: str) -> str:
    candidate = re.sub(r"\s+", " ", value).strip()
    if not candidate:
        return ""

    embedded = re.search(r"<[^<>]+>", candidate)
    if embedded:
        candidate = embedded.group(0)

    if candidate.startswith("<") and candidate.endswith(">"):
        inner = candidate[1:-1].strip()
        if "@" in inner and " " not in inner:
            return f"<{inner}>"
        return ""

    if "@" in candidate and " " not in candidate:
        return f"<{candidate}>"

    return ""


def parse_reference_ids(value: str) -> list[str]:
    found = [normalize_message_id(match.group(0)) for match in re.finditer(r"<[^<>]+>", value or "")]
    cleaned = [message_id for message_id in found if message_id]
    if cleaned:
        return cleaned

    fallback = normalize_message_id(value)
    if fallback:
        return [fallback]

    return []


def extract_parent_message_id(item: dict[str, Any]) -> str:
    for field_name in SOURCE_MESSAGE_FIELDS:
        candidate = normalize_message_id(str(item.get(field_name, "")))
        if candidate:
            return candidate
    return ""


def extract_reference_ids(item: dict[str, Any]) -> list[str]:
    for field_name in SOURCE_REFERENCES_FIELDS:
        raw_value = item.get(field_name, "")
        if isinstance(raw_value, list):
            joined = " ".join(str(entry) for entry in raw_value)
        else:
            joined = str(raw_value)

        parsed = parse_reference_ids(joined)
        if parsed:
            return parsed

    return []


def get_client_config() -> dict[str, Any]:
    raw_config = os.getenv(CLIENT_CONFIG_ENV, "").strip()
    if raw_config:
        try:
            parsed = json.loads(raw_config)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{CLIENT_CONFIG_ENV} contains invalid JSON.") from exc

        if not isinstance(parsed, dict) or "installed" not in parsed:
            raise ValueError(f"{CLIENT_CONFIG_ENV} must contain a Google OAuth client JSON object with an installed section.")

        return parsed

    client_id = os.getenv(CLIENT_ID_ENV, "").strip()
    client_secret = os.getenv(CLIENT_SECRET_ENV, "").strip()

    missing = [name for name, value in ((CLIENT_ID_ENV, client_id), (CLIENT_SECRET_ENV, client_secret)) if not value]
    if missing:
        raise EnvironmentError(
            "Missing required OAuth environment variables: "
            + ", ".join(missing)
            + f". Set them in your environment, or provide {CLIENT_CONFIG_ENV}."
        )

    project_id = os.getenv(PROJECT_ID_ENV, "").strip() or "local-gmail-client"

    return {
        "installed": {
            "client_id": client_id,
            "project_id": project_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
        }
    }


def get_credentials() -> Credentials:
    creds: Credentials | None = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(get_client_config(), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_reference_header(reference_ids: list[str], parent_message_id: str) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in [*reference_ids, parent_message_id]:
        if not candidate or candidate in seen:
            continue
        ordered.append(candidate)
        seen.add(candidate)

    return " ".join(ordered)


def build_raw_message(
    to: str,
    subject: str,
    body: str,
    source_ref: str,
    thread_id: str,
    parent_message_id: str,
    reference_ids: list[str],
) -> dict[str, Any]:
    message = MIMEText(body, _charset="utf-8")
    message["To"] = to
    message["Subject"] = subject
    message[MANAGED_HEADER] = MANAGED_VALUE
    if source_ref:
        message[SOURCE_REF_HEADER] = source_ref
    if parent_message_id:
        message["In-Reply-To"] = parent_message_id

    references_header = build_reference_header(reference_ids=reference_ids, parent_message_id=parent_message_id)
    if references_header:
        message["References"] = references_header

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    request_body: dict[str, Any] = {"message": {"raw": raw}}
    if thread_id:
        request_body["message"]["threadId"] = thread_id
    return request_body


def header_map(headers: list[dict[str, str]]) -> dict[str, str]:
    return {str(header.get("name", "")).lower(): str(header.get("value", "")) for header in headers}


def decode_part_data(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def extract_plain_text(payload: dict[str, Any]) -> str:
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if mime == "text/plain" and data:
        return decode_part_data(data)

    for part in payload.get("parts", []) or []:
        text = extract_plain_text(part)
        if text:
            return text

    if data:
        return decode_part_data(data)

    return ""


def iter_full_drafts(service: Any) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        response = (
            service.users()
            .drafts()
            .list(userId="me", maxResults=500, pageToken=page_token)
            .execute()
        )
        batch = response.get("drafts", [])

        for item in batch:
            draft_id = item.get("id")
            if not draft_id:
                continue

            full_draft = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="full")
                .execute()
            )
            drafts.append(full_draft)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return drafts


def internal_date_value(raw_value: Any) -> int:
    try:
        return int(str(raw_value))
    except (TypeError, ValueError):
        return 0


def remove_superseded_drafts(service: Any, target_source_refs: set[str], target_fallback_keys: set[str]) -> int:
    if not target_source_refs and not target_fallback_keys:
        return 0

    grouped: dict[str, list[tuple[int, str, str, str]]] = {}
    for draft in iter_full_drafts(service):
        draft_id = str(draft.get("id", "")).strip()
        if not draft_id:
            continue

        message = draft.get("message", {})
        payload = message.get("payload", {})
        headers = header_map(payload.get("headers", []))

        managed_value = headers.get(MANAGED_HEADER.lower(), "").strip().lower()
        if managed_value != MANAGED_VALUE:
            continue

        source_ref = headers.get(SOURCE_REF_HEADER.lower(), "").strip()
        subject = headers.get("subject", "")
        group_key = ""
        reason = ""
        if source_ref and source_ref in target_source_refs:
            group_key = f"source:{source_ref}"
            reason = source_ref
        else:
            to = headers.get("to", "")
            body = extract_plain_text(payload) or str(message.get("snippet", ""))
            content_key = build_draft_key(to=to, subject=subject, body=body)
            if content_key in target_fallback_keys:
                group_key = f"content:{content_key}"
                reason = subject or "(no subject)"

        if not group_key:
            continue

        created_at = internal_date_value(message.get("internalDate"))
        grouped.setdefault(group_key, []).append((created_at, draft_id, subject, reason))

    removed = 0
    for entries in grouped.values():
        if len(entries) <= 1:
            continue

        # Keep the newest draft and remove older superseded versions.
        entries.sort(key=lambda item: (item[0], item[1]))
        for _, draft_id, subject, reason in entries[:-1]:
            service.users().drafts().delete(userId="me", id=draft_id).execute()
            removed += 1
            print(f"Removed superseded draft {draft_id}: {subject} ({reason})")

    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Gmail drafts from reply-drafts.json.")
    parser.add_argument(
        "drafts_json",
        help=(
            "Path to JSON array with to/subject/body and optional source references "
            "(source_ref, source_thread_id/thread_id, source_message_id/internet_message_id/message_id, source_references/references). "
            "For proper Gmail threading, source_message_id should be the RFC Message-ID value."
        ),
    )
    args = parser.parse_args()

    drafts_path = Path(args.drafts_json)
    items = json.loads(drafts_path.read_text(encoding="utf-8"))

    if not isinstance(items, list):
        raise ValueError("Draft JSON must contain a top-level array.")

    service = build("gmail", "v1", credentials=get_credentials())

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
        source_thread_id = extract_thread_id(item)
        source_message_id = extract_parent_message_id(item)
        source_references = extract_reference_ids(item)
        if source_ref:
            target_source_refs.add(source_ref)
        else:
            target_fallback_keys.add(build_draft_key(to=to, subject=subject, body=body))

        draft = build_raw_message(
            to=to,
            subject=subject,
            body=body,
            source_ref=source_ref,
            thread_id=source_thread_id,
            parent_message_id=source_message_id,
            reference_ids=source_references,
        )
        service.users().drafts().create(userId="me", body=draft).execute()
        created += 1
        print(f"Created draft {created}: {subject}")

    removed = remove_superseded_drafts(
        service=service,
        target_source_refs=target_source_refs,
        target_fallback_keys=target_fallback_keys,
    )
    print(f"Created {created} drafts.")
    print(f"Removed {removed} superseded draft(s).")


if __name__ == "__main__":
    main()
