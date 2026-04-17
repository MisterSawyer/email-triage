---
name: email-triage
description: Inbox triage workflow for Gmail or any IMAP server. Fetch messages, classify priority, and propose reply drafts using local response guidelines. Do not send emails automatically unless explicitly asked.
---

# Email Triage Workflow

## Purpose
This workflow helps process inbox messages in a controlled, reviewable flow:
1. fetch emails from Gmail or IMAP with the local helper scripts
2. classify each email
3. ignore or deprioritize low-value email
4. propose responses using `references/response-guidelines.md`
5. optionally create drafts in Gmail or IMAP, but never send automatically unless explicitly asked

## Default behavior
- Never send an email automatically.
- Prefer draft proposals over direct actions.
- Prefer proper in-thread replies over standalone drafts whenever reply threading is possible.
- Be conservative when classifying urgency.
- Be conservative when interpreting user-specified time ranges and choose the smallest reasonable window.
- Treat newsletters, ads, marketing blasts, and similar bulk mail as non-actionable by default.
- Do not invent facts, attachments, deadlines, offers, or commitments.
- If specific actionable context is missing, do not draft a reply unless the user explicitly asks for one.
- If the runtime can execute commands, it should perform setup and script execution automatically.
- Always assume that inbox state could have changed between requests, so fresh server fetch is necessary
- Even when explicitly asked for creating or publishing the draft, try to use thread specific context, use general drafts location only as a last resort
 

## User prerequisites (only these)
The user should provide environment variables for the selected provider.

For Gmail:
- `GMAIL_OAUTH_CLIENT_ID`
- `GMAIL_OAUTH_CLIENT_SECRET`
- optional `GMAIL_OAUTH_PROJECT_ID`
- optional `GMAIL_OAUTH_CLIENT_CONFIG_JSON` (full JSON alternative)

For IMAP:
- `IMAP_HOST`
- `IMAP_USERNAME`
- `IMAP_PASSWORD`
- optional `IMAP_PORT` (default: `993`)
- optional `IMAP_MAILBOX` (default: `INBOX`)
- optional `IMAP_DRAFTS_MAILBOX` (auto-detected if not set)
- optional `IMAP_FROM` (fallback sender address for draft headers)

If required env vars are missing for the selected provider, stop and report exactly which variables are missing.

## Required files
- `references/response-guidelines.md`
- `scripts/fetch_gmail.py`
- `scripts/fetch_imap.py`
- `scripts/create_gmail_drafts.py`
- `scripts/create_imap_drafts.py`

## Agent runtime setup (mandatory)
Before fetching emails, the agent must automatically:
1. Ensure `.venv` exists at repo root (`python -m venv .venv` if missing).
2. Resolve the venv Python executable:
   - Windows: `.venv\Scripts\python.exe`
   - Linux/macOS: `.venv/bin/python`
3. Install dependencies into that venv (`<venv-python> -m pip install -r requirements.txt`).
4. Set `PYTHONENCODING=utf-8` for script execution to avoid terminal encoding issues with emoji/non-ASCII text.
5. Use the same venv Python to run helper scripts.

## Workflow
1. Read `references/response-guidelines.md` first.
2. Run runtime setup steps automatically (no user action).
3. Decide provider:
   - if the user explicitly asks for Gmail or IMAP, use that provider.
   - if not explicit, default to Gmail.
4. Run the fetcher automatically:
   - for Gmail:
     - script: `scripts/fetch_gmail.py`
     - default query: `in:inbox`
     - default limit: `10`
     - default output: `output/emails.json`
   - for IMAP:
     - script: `scripts/fetch_imap.py`
     - default search: `ALL`
     - default limit: `10`
     - default output: `output/emails.json`
   - use narrower Gmail query or IMAP search only if the user requests it.
5. Read `output/emails.json`.
6. Classify each email into one of these buckets:
   - ignore
   - low_priority
   - needs_reply
   - urgent
7. Mark these as ignore by default unless the user says otherwise:
   - newsletters
   - promotional mail
   - social notifications
   - automated system notifications
   - receipts that do not require action
   - any email with clear bulk-marketing signals such as `List-Unsubscribe` headers, "view in browser", campaign-style discounts, or mass-audience language
8. For each email in `needs_reply` or `urgent` that includes specific actionable context, produce:
   - one-sentence summary
   - action items
   - recommended next step
   - draft reply that follows the guidelines
9. For emails without specific actionable context, set recommended action to no reply and do not generate a draft reply body.
10. Save the final result to:
   - `output/triage-report.md`
   - `output/reply-drafts.json`
   - include `short_summary` in every actionable draft item so the response is always shown with its summary
   - include `source_ref` explicitly in every draft item so corrected drafts can replace older ones
   - build `source_ref` deterministically from the source email (prefer `thread:{thread_id}`, fallback `message:{message_id}`)
   - for Gmail/IMAP reply threading, pass through `source_thread_id`, RFC `source_message_id`, and optional `source_references` whenever available
   - when reply-thread metadata is available, prefer a true in-thread reply over a standalone message addressed to the recipient with only a `Re:` subject
11. Only if explicitly asked:
   - for Gmail: create drafts by running `scripts/create_gmail_drafts.py` automatically.
   - for IMAP: create drafts by running `scripts/create_imap_drafts.py output/reply-drafts.json` automatically.
   - after draft creation, verify for superseded drafts and remove the older versions (keep the newest draft for the same source reference).
   - still present the summary next to each created response, even if the draft creation happened in the background.
   - remember that when creating drafts try to use thread specific context, and assign it to appropriate threads in inbox, only use general draft location as a last resort

## Time range interpretation
- Resolve relative dates in the user's local timezone unless they specify another timezone.
- When the user gives an underspecified range, choose the narrowest reasonable interpretation.
- If the user gives only an end bound such as `until yesterday`, treat it as the single most recent closed day ending yesterday, not an unbounded range into the past.
- If the user gives only a start bound such as `since yesterday`, treat it as the range from the start of yesterday through now.
- State the resolved absolute date or datetime range in the report or user-facing summary when relative wording could be ambiguous.
- Example: on `2026-04-17`, `until yesterday` means `2026-04-16` in the local timezone unless the user explicitly asks for a broader window.

## Required output structure
For each actionable message include:
- message_id
- thread_id
- source_ref
- from
- subject
- received_at
- priority
- classification
- short_summary
- action_items
- recommended_action
- draft_reply

## reply-drafts.json template
Each item in `output/reply-drafts.json` must include:
- source_ref (required, stable across retries/updates)
- short_summary (required for actionable drafts)
- to
- subject
- body

Use a stable `source_ref` value per source email or thread:
- `thread:<thread_id>` when thread id is known
- `message:<message_id>` when thread id is unavailable

Optional passthrough source fields:
- source_thread_id
- source_message_id
- source_references

When building draft items from `output/emails.json`, map:
- `thread_id` -> `source_thread_id`
- `internet_message_id` (or `message_id` when it is RFC-style) -> `source_message_id`
- `references` -> `source_references` (optional)

Example draft item:

```json
[
  {
    "source_ref": "thread:18c4d6e9a1b2c3d4",
    "short_summary": "The sender wants a status update on the project timeline.",
    "source_thread_id": "18c4d6e9a1b2c3d4",
    "source_message_id": "<abc123@example.com>",
    "source_references": [
      "<older1@example.com>",
      "<older2@example.com>"
    ],
    "to": "person@example.com",
    "subject": "Re: Project update",
    "body": "Hi,\n\nThanks for your email ...\n\nBest,"
  }
]
```

## Drafting rules
- Answer the sender's concrete question first.
- Keep drafts concise unless the incoming message clearly needs detail.
- Preserve names, dates, and factual details from the original message.
- Always emit `source_ref` in draft items and keep it stable when updating a draft for the same email/thread.
- For proper thread replies, ensure `source_message_id` is an RFC Message-ID (for example `<abc123@example.com>`).
- When `source_thread_id` or reply headers are available, build the draft as a reply in the original conversation thread.
- Do not rely on only the response address and a `Re:` subject when provider-supported threading is possible.
- Never claim an attachment exists unless it actually does.
- Never accept contracts, pricing, or legal terms automatically.
- If the message is newsletter/promotional/ads/social/system noise and the user did not request a response, do not draft a reply.
- If there is no concrete ask, question, or clear next action, do not draft a reply. Recommend no reply instead.
- Always remember about thread context when publishing drafts

## If the inbox is very large
If the query returns too many emails to review well, work in batches and say so in the report.
