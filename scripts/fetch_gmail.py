from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any, Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from terminal_encoding import configure_terminal_encoding

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "emails.json"
TOKEN_PATH = ROOT / "token.json"
CLIENT_CONFIG_ENV = "GMAIL_OAUTH_CLIENT_CONFIG_JSON"
CLIENT_ID_ENV = "GMAIL_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "GMAIL_OAUTH_CLIENT_SECRET"
PROJECT_ID_ENV = "GMAIL_OAUTH_PROJECT_ID"

configure_terminal_encoding()


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


def header_map(headers: Iterable[dict[str, str]]) -> dict[str, str]:
    return {header["name"].lower(): header["value"] for header in headers}


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


def extract_html_text(payload: dict[str, Any]) -> str:
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if mime == "text/html" and data:
        return decode_part_data(data)

    for part in payload.get("parts", []) or []:
        html = extract_html_text(part)
        if html:
            return html

    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Gmail messages into a local JSON file.")
    parser.add_argument("--query", default="in:inbox", help="Gmail search query.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum number of messages to fetch.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    service = build("gmail", "v1", credentials=get_credentials())

    collected: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(collected) < args.limit:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=args.query,
                maxResults=min(500, args.limit - len(collected)),
                pageToken=page_token,
            )
            .execute()
        )

        batch = response.get("messages", [])
        if not batch:
            break

        for item in batch:
            full = (
                service.users()
                .messages()
                .get(userId="me", id=item["id"], format="full")
                .execute()
            )

            payload = full.get("payload", {})
            headers = header_map(payload.get("headers", []))
            gmail_message_id = str(full.get("id", "")).strip()
            internet_message_id = str(headers.get("message-id", "")).strip()

            collected.append(
                {
                    "message_id": internet_message_id or gmail_message_id,
                    "gmail_message_id": gmail_message_id,
                    "internet_message_id": internet_message_id,
                    "thread_id": full.get("threadId", ""),
                    "label_ids": full.get("labelIds", []),
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "cc": headers.get("cc", ""),
                    "subject": headers.get("subject", ""),
                    "received_at": headers.get("date", ""),
                    "in_reply_to": headers.get("in-reply-to", ""),
                    "references": headers.get("references", ""),
                    "snippet": full.get("snippet", ""),
                    "body_text": extract_plain_text(payload),
                    "body_html": extract_html_text(payload),
                }
            )

            if len(collected) >= args.limit:
                break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    output_path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(collected)} emails to {output_path}")


if __name__ == "__main__":
    main()
