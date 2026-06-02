"""
ROOT Health Check.

Runs as a daily cron job or on-demand. Checks all background systems
and outputs a health report. Designed to catch silent failures early.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def check_root_index(root_dir: Path) -> dict:
    """Check ROOT index freshness and integrity."""
    db_path = root_dir / "data" / "root.db"
    if not db_path.exists() and (root_dir / "data" / "root.db").is_symlink():
        db_path = (root_dir / "data" / "root.db").resolve()

    if not db_path.exists():
        return {"status": "FAIL", "message": "root.db not found", "details": str(db_path)}

    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()

        # Last indexed time
        c.execute("SELECT MAX(indexed_at) FROM notes")
        last_indexed = c.fetchone()[0]

        # Note counts by source
        c.execute("SELECT source_type, COUNT(*) FROM notes GROUP BY source_type")
        sources = {r[0]: r[1] for r in c.fetchall()}

        # Entity stats
        c.execute("SELECT COUNT(*) FROM entities")
        entities = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM relations")
        relations = c.fetchone()[0]

        # Notes needing extraction
        c.execute("""SELECT COUNT(*) FROM notes n
                     LEFT JOIN entity_extractions ee ON ee.note_id = n.id
                     WHERE ee.note_id IS NULL OR ee.content_hash != n.content_hash""")
        pending_extraction = c.fetchone()[0]

        # Orphaned data
        c.execute("""SELECT COUNT(*) FROM entity_extractions
                     WHERE note_id NOT IN (SELECT id FROM notes)""")
        orphaned_extractions = c.fetchone()[0]

        conn.close()

        # Freshness check
        now = datetime.now(timezone.utc)
        if last_indexed:
            last_dt = datetime.fromisoformat(last_indexed.replace("Z", "+00:00"))
            hours_stale = (now - last_dt).total_seconds() / 3600
        else:
            hours_stale = 999

        status = "OK" if hours_stale < 6 else "WARN" if hours_stale < 24 else "FAIL"

        return {
            "status": status,
            "last_indexed": last_indexed,
            "hours_since_index": round(hours_stale, 1),
            "notes": sources,
            "total_notes": sum(sources.values()),
            "entities": entities,
            "relations": relations,
            "pending_extraction": pending_extraction,
            "orphaned_extractions": orphaned_extractions,
        }
    except Exception as e:
        return {"status": "FAIL", "message": str(e)}


def check_cron_jobs() -> dict:
    """Check if expected cron jobs are registered."""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and not l.strip().startswith("#")]

        expected = {
            "granola-obsidian-sync": "granola-obsidian-sync",
            "root-indexer": "indexer.py",
        }

        found = {}
        for name, pattern in expected.items():
            found[name] = any(pattern in line for line in lines)

        all_ok = all(found.values())
        return {
            "status": "OK" if all_ok else "FAIL",
            "total_jobs": len(lines),
            "expected_jobs": found,
        }
    except Exception as e:
        return {"status": "FAIL", "message": str(e)}


def check_cron_logs() -> dict:
    """Check cron job log files for recent errors."""
    log_dir = Path.home() / "Library" / "Logs"
    checks = {}

    log_pairs = {
        "root-indexer": (log_dir / "root-indexer.log", log_dir / "root-indexer.err"),
        "granola-sync": (log_dir / "granola-obsidian-sync.log", log_dir / "granola-obsidian-sync.err"),
    }

    for name, (out_log, err_log) in log_pairs.items():
        check = {"out_exists": out_log.exists(), "err_exists": err_log.exists()}

        if err_log.exists():
            err_content = err_log.read_text().strip()
            # Only flag real errors, not INFO/DEBUG messages routed to stderr
            real_errors = [
                line for line in err_content.splitlines()[-10:]
                if line.strip()
                and "[INFO]" not in line
                and "[DEBUG]" not in line
                and "Done:" not in line
                and "synced," not in line
            ]
            check["has_errors"] = bool(real_errors)
            if real_errors:
                check["last_error"] = "\n".join(real_errors[-3:])
        else:
            check["has_errors"] = False

        # Use the most recently modified of output or error log for freshness
        latest_mtime = 0
        for log_file in (out_log, err_log):
            if log_file.exists():
                latest_mtime = max(latest_mtime, log_file.stat().st_mtime)

        if latest_mtime > 0:
            mod_time = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
            hours_ago = (datetime.now(timezone.utc) - mod_time).total_seconds() / 3600
            check["last_output_hours_ago"] = round(hours_ago, 1)
            check["status"] = "OK" if hours_ago < 6 else "WARN" if hours_ago < 24 else "STALE"
        else:
            check["status"] = "NO_LOG"

        checks[name] = check

    all_ok = all(c.get("status") == "OK" and not c.get("has_errors") for c in checks.values())
    return {
        "status": "OK" if all_ok else "WARN",
        "jobs": checks,
    }


def check_mcp_server() -> dict:
    """Check if ROOT MCP server is registered and responsive."""
    mcp_json = Path(__file__).parent.parent.parent / ".mcp.json"

    if not mcp_json.exists():
        return {"status": "FAIL", "message": ".mcp.json not found"}

    try:
        config = json.loads(mcp_json.read_text())
        servers = config.get("mcpServers", {})
        root_registered = "root" in servers

        return {
            "status": "OK" if root_registered else "WARN",
            "registered_servers": list(servers.keys()),
            "root_registered": root_registered,
        }
    except Exception as e:
        return {"status": "FAIL", "message": str(e)}


def check_memory_system() -> dict:
    """Check memory system health."""
    # The project slug is derived from the workspace path (Claude Code convention).
    # Adapt this path to match your local workspace if needed.
    project_slug = Path(__file__).parent.parent.parent.as_posix().replace("/", "-").lstrip("-")
    memory_dir = Path.home() / ".claude" / "projects" / project_slug / "memory"

    if not memory_dir.exists():
        return {"status": "FAIL", "message": "Memory directory not found"}

    memory_files = list(memory_dir.glob("*.md"))
    memory_index = memory_dir / "MEMORY.md"

    # Check freshness of most recent memory
    newest = max((f.stat().st_mtime for f in memory_files), default=0)
    if newest:
        hours_since = (datetime.now(timezone.utc).timestamp() - newest) / 3600
    else:
        hours_since = 999

    return {
        "status": "OK" if memory_files else "WARN",
        "total_files": len(memory_files),
        "index_exists": memory_index.exists(),
        "newest_memory_hours_ago": round(hours_since, 1),
    }


def check_handoffs() -> dict:
    """Check handoff system health."""
    handoff_dir = Path(__file__).parent.parent.parent / ".claude" / "handoffs"

    if not handoff_dir.exists():
        return {"status": "WARN", "message": "Handoffs directory not found"}

    handoffs = sorted(handoff_dir.glob("*.md"), key=lambda f: f.name, reverse=True)

    if not handoffs:
        return {"status": "WARN", "total": 0}

    latest = handoffs[0]
    # Extract date from filename (YYYY-MM-DD-topic.md)
    date_str = latest.name[:10]
    try:
        latest_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_since = (datetime.now(timezone.utc) - latest_date).days
    except ValueError:
        days_since = -1

    return {
        "status": "OK" if days_since <= 3 else "WARN",
        "total": len(handoffs),
        "latest": latest.name,
        "days_since_latest": days_since,
    }


def check_vault_access() -> dict:
    """Check if Obsidian vault is accessible."""
    vault_name = os.environ.get("ROOT_VAULT_NAME", "My Obsidian Vault")
    vault_path = Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / vault_name

    if not vault_path.exists():
        return {"status": "FAIL", "message": "Vault path does not exist"}

    try:
        items = list(vault_path.iterdir())
        return {
            "status": "OK" if len(items) > 0 else "FAIL",
            "accessible": True,
            "top_level_items": len(items),
        }
    except PermissionError:
        return {"status": "FAIL", "message": "Permission denied (Full Disk Access needed)"}
    except Exception as e:
        return {"status": "FAIL", "message": str(e)}


def check_env_file(root_dir: Path) -> dict:
    """Check .env file exists and has required keys."""
    env_file = root_dir / ".env"

    if not env_file.exists():
        return {"status": "FAIL", "message": ".env not found"}

    content = env_file.read_text()
    has_anthropic = "ANTHROPIC_API_KEY=" in content and len(content.split("ANTHROPIC_API_KEY=")[1].strip()) > 10

    return {
        "status": "OK" if has_anthropic else "FAIL",
        "has_anthropic_key": has_anthropic,
    }


def main():
    root_dir = Path(__file__).parent
    now = datetime.now(timezone.utc)

    print(f"{'=' * 60}")
    print(f"  ROOT Health Check  |  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}")
    print()

    checks = {
        "ROOT Index": check_root_index(root_dir),
        "Vault Access": check_vault_access(),
        "Environment": check_env_file(root_dir),
        "Cron Jobs": check_cron_jobs(),
        "Cron Logs": check_cron_logs(),
        "MCP Server": check_mcp_server(),
        "Memory System": check_memory_system(),
        "Handoffs": check_handoffs(),
    }

    status_icons = {"OK": "\033[32m[OK]\033[0m", "WARN": "\033[33m[WARN]\033[0m", "FAIL": "\033[31m[FAIL]\033[0m", "STALE": "\033[33m[STALE]\033[0m", "NO_LOG": "\033[33m[NO_LOG]\033[0m"}

    for name, result in checks.items():
        status = result.get("status", "UNKNOWN")
        icon = status_icons.get(status, f"[{status}]")
        print(f"  {icon}  {name}")

        # Print key details based on check type
        if name == "ROOT Index" and status != "FAIL":
            print(f"         Last indexed: {result.get('hours_since_index', '?')}h ago")
            print(f"         Notes: {result.get('total_notes', '?')} | Entities: {result.get('entities', '?')} | Relations: {result.get('relations', '?')}")
            if result.get("pending_extraction", 0) > 0:
                print(f"         Pending extraction: {result['pending_extraction']} notes")
            if result.get("orphaned_extractions", 0) > 0:
                print(f"         Orphaned extractions: {result['orphaned_extractions']} (needs cleanup)")

        elif name == "Cron Jobs":
            for job_name, found in result.get("expected_jobs", {}).items():
                job_icon = "\033[32m+\033[0m" if found else "\033[31m-\033[0m"
                print(f"         {job_icon} {job_name}")

        elif name == "Cron Logs":
            for job_name, job_check in result.get("jobs", {}).items():
                job_status = job_check.get("status", "?")
                has_err = job_check.get("has_errors", False)
                j_icon = status_icons.get(job_status, f"[{job_status}]")
                err_flag = " \033[31m(errors in log)\033[0m" if has_err else ""
                hours = job_check.get("last_output_hours_ago", "?")
                print(f"         {j_icon} {job_name} (last output: {hours}h ago){err_flag}")

        elif name == "Memory System":
            print(f"         {result.get('total_files', 0)} files | Index: {'yes' if result.get('index_exists') else 'no'}")

        elif name == "Handoffs":
            print(f"         {result.get('total', 0)} total | Latest: {result.get('latest', 'none')} ({result.get('days_since_latest', '?')}d ago)")

        elif "message" in result:
            print(f"         {result['message']}")

        print()

    # Summary
    statuses = [r.get("status") for r in checks.values()]
    fails = statuses.count("FAIL")
    warns = statuses.count("WARN")

    if fails > 0:
        print(f"  \033[31mHealth: {fails} FAIL, {warns} WARN\033[0m")
    elif warns > 0:
        print(f"  \033[33mHealth: All systems up, {warns} warnings\033[0m")
    else:
        print(f"  \033[32mHealth: All systems nominal\033[0m")

    print()

    # Send Slack alert on failures
    if fails > 0:
        alert_lines = []
        for name, result in checks.items():
            if result.get("status") == "FAIL":
                msg = result.get("message", "")
                alert_lines.append(f"*{name}*: FAIL{' — ' + msg if msg else ''}")
        alert_text = "\n".join(alert_lines)
        send_slack_alert(
            f":rotating_light: *ROOT Health Check FAILED*\n"
            f"{fails} failure(s), {warns} warning(s)\n\n{alert_text}"
        )

    # Return exit code for cron monitoring
    sys.exit(1 if fails > 0 else 0)


def send_slack_alert(message: str) -> None:
    """Write alert file for Claude Code to pick up and send via Telegram.

    The health check runs via cron (no MCP access). Alert files are
    picked up by the next Claude Code session, which sends them via
    Telegram and deletes the file.

    If TELEGRAM_BOT_TOKEN is available via openclaw config, also sends directly.
    """
    alert_dir = Path(__file__).parent / "alerts"
    alert_dir.mkdir(exist_ok=True)
    alert_file = alert_dir / f"alert-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
    alert_file.write_text(f"# Health Check Alert\n\n{message}\n")
    print(f"  Alert written to: {alert_file}")

    # Try direct Telegram send if bot token available
    token = ""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
    if openclaw_config.exists():
        try:
            with open(openclaw_config) as f:
                oc = json.loads(f.read())
                token = oc.get("telegram_bot_token", "")
        except Exception:
            pass

    if token:
        # Strip Slack markdown formatting for Telegram
        clean_message = message.replace(":rotating_light:", "").replace("*", "**")
        payload = json.dumps({
            "chat_id": chat_id,
            "text": clean_message,
            "parse_mode": "Markdown",
        }).encode("utf-8")
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    print(f"  Telegram alert sent directly")
                    alert_file.unlink()  # Remove file since delivery succeeded
        except Exception as e:
            print(f"  Direct Telegram send failed (alert file preserved): {e}")

    # Always send macOS notification (works from cron, no auth needed)
    _send_macos_notification(message)


def _send_macos_notification(message: str) -> None:
    """Send a macOS notification center alert. Works from cron without any tokens."""
    # Strip markdown formatting for the notification
    clean = message.replace("*", "").replace(":rotating_light:", "").replace("_", "").strip()
    # Take first 200 chars for the notification body
    title = "ROOT Health Check FAILED"
    body = clean[:200].replace('"', '\\"').replace('\n', ' ')

    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}" sound name "Basso"'],
            capture_output=True, timeout=5,
        )
        print(f"  macOS notification sent")
    except Exception as e:
        print(f"  macOS notification failed: {e}")


if __name__ == "__main__":
    main()
