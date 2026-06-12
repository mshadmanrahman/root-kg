#!/usr/bin/env python3
"""root-merge: safely fold entity shards into a canonical entity.

The reusable version of the 2026-06-12 one-off curated merge. Resolves names to
ids, then on --execute boots out the writer daemons, backs up the DB, runs the
SAFE db.merge_entities() for each loser inside its own transaction, runs an
integrity_check, and restarts the daemons.

Usage:
  python merge_cli.py --keep "Ric Prestage" --merge "Ric" "Rick"          # dry-run
  python merge_cli.py --keep "Ric Prestage" --merge "Ric" "Rick" --execute
  python merge_cli.py --keep 4009 --merge 53 --execute                    # by id

Names are resolved case-insensitively against entity names; if a name matches
several entities the candidates are printed and the run aborts (be explicit with
an id) -- so we never merge the wrong shard.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "data" / "root.db"
# Optional belt-and-suspenders: stop writer daemons during the merge. The merge
# is already atomic + WAL-safe + backed up, so this is opt-in. Set ROOT_DAEMONS
# to a comma-separated list of launchd labels (macOS) to enable, e.g.
#   ROOT_DAEMONS="com.you.rootd,com.you.root-graph" python merge_cli.py ...
DAEMONS = [d.strip() for d in os.environ.get("ROOT_DAEMONS", "").split(",") if d.strip()]

sys.path.insert(0, str(HERE))
from db import RootDB  # noqa: E402


def resolve_arg(conn, token: str):
    """A bare integer is an id; otherwise resolve by exact (case-insensitive) name.
    Returns an id, or prints candidates and returns None on ambiguity/miss."""
    if token.isdigit():
        row = conn.execute("SELECT id, name FROM entities WHERE id=?", (int(token),)).fetchone()
        return row["id"] if row else _miss(token, [])
    rows = conn.execute("SELECT id, name, entity_type FROM entities WHERE name=? COLLATE NOCASE", (token,)).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    return _miss(token, rows)


def _miss(token, rows):
    if not rows:
        print(f"  !! '{token}' matched no entity (use an explicit id, or check spelling)")
    else:
        print(f"  !! '{token}' is ambiguous ({len(rows)} matches) -- pass an explicit id:")
        for r in rows:
            print(f"       {r['id']}  {r['name']} ({r['entity_type']})")
    return None


def relcount(conn, eid):
    return conn.execute("SELECT COUNT(*) FROM relations WHERE entity_a_id=? OR entity_b_id=?", (eid, eid)).fetchone()[0]


def launchctl(action, label):
    uid = os.getuid()
    plist = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    if action == "stop":
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    else:
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", required=True, help="canonical entity (name or id) to keep")
    ap.add_argument("--merge", required=True, nargs="+", help="shard(s) (name or id) to fold in")
    ap.add_argument("--execute", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()

    conn = RootDB(str(DB)).conn
    keep_id = resolve_arg(conn, args.keep)
    loser_ids = [resolve_arg(conn, m) for m in args.merge]
    if keep_id is None or any(l is None for l in loser_ids):
        print("Aborted: unresolved or ambiguous name(s) above.")
        return 1
    loser_ids = [l for l in loser_ids if l != keep_id]

    kname = conn.execute("SELECT name FROM entities WHERE id=?", (keep_id,)).fetchone()["name"]
    print(f"KEEP {keep_id} '{kname}' ({relcount(conn, keep_id)} rels)")
    for l in loser_ids:
        ln = conn.execute("SELECT name FROM entities WHERE id=?", (l,)).fetchone()["name"]
        print(f"   <- {l} '{ln}' ({relcount(conn, l)} rels)")

    if not args.execute:
        print("\nDry run. Re-run with --execute to perform the merge.")
        return 0

    conn.close()
    print("\nStopping daemons + backing up...")
    for d in DAEMONS:
        launchctl("stop", d)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DB.with_name(f"root.db.pre-merge-{ts}")
    subprocess.run(["sqlite3", str(DB), "PRAGMA wal_checkpoint(TRUNCATE);"], capture_output=True)
    subprocess.run(["sqlite3", str(DB), f".backup '{backup}'"], check=True)
    print(f"  backup: {backup.name}")

    try:
        db = RootDB(str(DB))
        for l in loser_ids:
            db.merge_entities(keep_id=keep_id, merge_id=l)
        ic = db.conn.execute("PRAGMA integrity_check").fetchone()[0]
        after = relcount(db.conn, keep_id)
        db.close()
        print(f"  merged -> '{kname}' now {after} rels | integrity_check: {ic}")
    finally:
        for d in DAEMONS:
            launchctl("start", d)
        print("Daemons restarted.")
    print("Done. Rollback if needed: restore", backup.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
