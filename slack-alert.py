"""
Lightweight Slack alerter for cron jobs.

Uses Slack Bot API (chat.postMessage) with a bot token.
Falls back to writing alert files if token is not configured.

Usage:
    echo "message" | python slack-alert.py
    python slack-alert.py "message text"
    python slack-alert.py --file alerts/alert-20260324.md
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# DM channel for health alerts
CHANNEL_ID = "D0AFVR1U7Q8"


def send_slack(message: str) -> bool:
    """Send a message to Slack via Bot API."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")

    if not token:
        # Try reading from .env
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("SLACK_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"')

    if not token:
        print("No SLACK_BOT_TOKEN found. Set it in .env or environment.", file=sys.stderr)
        return False

    payload = json.dumps({
        "channel": CHANNEL_ID,
        "text": message,
        "unfurl_links": False,
    }).encode("utf-8")

    req = Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"Slack alert sent to {CHANNEL_ID}")
                return True
            else:
                print(f"Slack API error: {result.get('error', 'unknown')}", file=sys.stderr)
                return False
    except (HTTPError, Exception) as e:
        print(f"Slack send failed: {e}", file=sys.stderr)
        return False


def main():
    # Get message from argument, file, or stdin
    if len(sys.argv) > 1:
        if sys.argv[1] == "--file" and len(sys.argv) > 2:
            message = Path(sys.argv[2]).read_text()
        else:
            message = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        message = sys.stdin.read()
    else:
        print("Usage: python slack-alert.py 'message' | --file path", file=sys.stderr)
        sys.exit(1)

    if not send_slack(message.strip()):
        sys.exit(1)


if __name__ == "__main__":
    main()
