#!/usr/bin/env python3
"""prismatic_rewrite_paths.py — rewrite local file references in text to clickable Cloudflare Access URLs.

Pipes text containing local file paths, publishes them, and replaces paths with clickable links.
If publishing fails, falls back to uploading the file directly to Telegram.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.request
import uuid
from typing import Any

# Telegram configuration from env
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_HOME_CHANNEL = os.environ.get("TELEGRAM_HOME_CHANNEL")


def upload_to_telegram(path: str) -> tuple[bool, str | None]:
    """Upload a file as a document to the Telegram Home Channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_HOME_CHANNEL:
        return False, "Telegram bot token or home channel not configured"

    try:
        boundary = uuid.uuid4().hex
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }

        with open(path, "rb") as f:
            file_content = f.read()

        file_name = os.path.basename(path)
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"

        part_list = []
        # chat_id
        part_list.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{TELEGRAM_HOME_CHANNEL}\r\n".encode("utf-8")
        )
        # caption
        caption = f"Fallback attachment: {file_name}"
        part_list.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8")
        )
        # document
        part_list.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{file_name}\"\r\nContent-Type: {mime_type}\r\n\r\n".encode("utf-8")
        )
        part_list.append(file_content)
        part_list.append(
            f"\r\n--{boundary}--\r\n".encode("utf-8")
        )

        body = b"".join(part_list)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data=body,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return True, None
            return False, data.get("description", "Unknown Telegram error")
    except Exception as e:
        return False, str(e)


def publish_path(path: str) -> tuple[str | None, str | None]:
    """Publish the local file using prismatic-publish CLI.

    Returns:
        (url, error_message)
    """
    try:
        # Check if the file is empty or missing
        if not os.path.exists(path):
            return None, "File does not exist"

        # Run the prismatic-publish tool
        result = subprocess.run(
            ["prismatic-publish", path, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                if isinstance(data, list) and len(data) > 0 and "url_raw" in data[0]:
                    return data[0]["url_raw"], None
                elif isinstance(data, dict) and "url_raw" in data:
                    return data["url_raw"], None
            except json.JSONDecodeError:
                # If JSON parsing fails, fall back to parsing raw stdout lines
                lines = result.stdout.strip().split("\n")
                if lines and lines[-1].startswith("http"):
                    return lines[-1], None

        # Publishing failed: extract stderr
        error_msg = result.stderr.strip() or f"exit code {result.returncode}"

        # Attempt Telegram upload fallback
        success, tg_err = upload_to_telegram(path)
        if success:
            return None, f"failed to publish ({error_msg}), uploaded to Telegram"
        else:
            return None, f"failed to publish ({error_msg}) and Telegram upload failed ({tg_err})"
    except Exception as e:
        # Handle unexpected publication error and attempt Telegram upload fallback
        success, tg_err = upload_to_telegram(path)
        if success:
            return None, f"error during publication ({e}), uploaded to Telegram"
        else:
            return None, f"error during publication ({e}) and Telegram upload failed ({tg_err})"


def rewrite_paths_in_text(
    text: str,
    emit_links_only: bool = False,
    processed_paths: list[dict[str, Any]] | None = None,
) -> str:
    """Scan text, detect local file paths, publish them, and replace paths with links."""
    # Pattern matching absolute paths or relative paths with extension or containing slash
    pattern = re.compile(r"/[a-zA-Z0-9_\-\./]+|[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\./]+")
    matches = list(pattern.finditer(text))
    
    # Track all published URLs for --emit-links mode
    published_urls = []

    # Process matches from right to left to preserve offsets
    for match in reversed(matches):
        candidate = match.group(0)
        start_idx = match.start()
        end_idx = match.end()

        # Exclude web URLs (http / https)
        prefix = text[max(0, start_idx - 8):start_idx]
        if "http" in prefix or "https" in prefix:
            continue

        # Clean trailing punctuation from candidate
        path = candidate
        trailing = ""
        while path and not os.path.exists(path) and path[-1] in ".,;:!?)]}*`\"'":
            trailing = path[-1] + trailing
            path = path[:-1]

        if not path or not os.path.exists(path):
            continue

        # Publish the file/directory
        url, error_msg = publish_path(path)

        if url:
            published_urls.append(url)

        if processed_paths is not None:
            processed_paths.append({
                "path": path,
                "url": url,
                "error_msg": error_msg,
                "fallback_uploaded": (url is None and error_msg is not None and "uploaded to Telegram" in error_msg)
            })

        if emit_links_only:
            continue

        # Determine replacement link format
        path_end_idx = end_idx - len(trailing)
        is_markdown_link = False
        if start_idx > 0 and path_end_idx < len(text):
            if text[start_idx - 1] == "(" and text[path_end_idx] == ")":
                is_markdown_link = True

        if url:
            if is_markdown_link:
                replacement = url
            else:
                filename = os.path.basename(path)
                replacement = f"[{filename}]({url})"
        else:
            filename = os.path.basename(path)
            # Say so explicitly
            if error_msg:
                replacement = f"[{filename} ({error_msg})]"
            else:
                replacement = f"[{filename} (failed to publish)]"

        text = text[:start_idx] + replacement + trailing + text[path_end_idx + len(trailing):]

    if emit_links_only:
        # In emit-links-only mode, we output the list of raw URLs (reversed to preserve original order)
        return "\n".join(reversed(published_urls))

    return text


class FredTelegramHelper:
    """Helper class for Fred to process replies and handle local file references for Telegram."""

    @staticmethod
    def prepare_reply(text: str) -> dict[str, Any]:
        """
        Process the message text, publishing any files.
        If a file is published successfully, it replaces the path with the Cloudflare URL as a Markdown link.
        If a file fails to publish, it uploads the file to Telegram as a fallback and explicitly notes this in the text.
        
        Returns:
            A dictionary containing:
              - "text": The processed/rewritten text.
              - "uploads": A list of dicts with details of uploaded/fallback files:
                [{"path": str, "success": bool, "error": str | None}]
        """
        processed_paths = []
        rewritten_text = rewrite_paths_in_text(text, processed_paths=processed_paths)
        
        uploads = []
        for item in processed_paths:
            if item["url"] is None:
                # Publishing failed, check if fallback upload was attempted
                success = item["fallback_uploaded"]
                uploads.append({
                    "path": item["path"],
                    "success": success,
                    "error": item["error_msg"]
                })
        
        return {
            "text": rewritten_text,
            "uploads": uploads
        }


def fred_prepare_reply(text: str) -> dict[str, Any]:
    """Convenience wrapper function for FredTelegramHelper.prepare_reply."""
    return FredTelegramHelper.prepare_reply(text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite local file references in text to clickable Cloudflare Access URLs."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to read input from. If empty, reads from stdin.",
    )
    parser.add_argument(
        "--emit-links",
        action="store_true",
        help="Only output the published URLs instead of rewriting text",
    )
    args = parser.parse_args()

    if args.files:
        content = ""
        for f_path in args.files:
            try:
                with open(f_path, "r", encoding="utf-8") as f:
                    content += f.read()
            except Exception as e:
                print(f"Error reading file {f_path}: {e}", file=sys.stderr)
                return 1
    else:
        content = sys.stdin.read()

    rewritten = rewrite_paths_in_text(content, emit_links_only=args.emit_links)
    print(rewritten, end="")
    return 0



if __name__ == "__main__":
    sys.exit(main())
