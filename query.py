"""
ROOT CLI Query — callable by OpenClaw agents via exec.

Usage:
    python query.py <tool> [args...]

Tools:
    open_loops [scope]          — Unresolved action items (scope: all|meetings|vault)
    themes [scope]              — Discover themes from recent notes (scope: all|meetings|vault)
    blind_spots                 — Entities with declining activity
    weekly_digest               — Weekly knowledge graph activity summary
    decision_trail <topic>      — How decisions evolved around a topic
    project_pulse <project>     — Recent activity for a project
    search <query>              — Semantic search across all sources
"""

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import RootDB

# Tools that need embeddings vs pure-SQL tools
_EMBEDDING_TOOLS = {"open_loops", "themes", "decision_trail", "project_pulse", "search"}

logging.basicConfig(
    format="%(asctime)s [ROOT] %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
logger = logging.getLogger("root.query")


def _get_embedder():
    """Lazy-load embedder only when needed."""
    from embeddings import Embedder
    return Embedder()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    tool = sys.argv[1]
    args = sys.argv[2:]

    start = time.time()
    logger.info("tool=%s args=%s", tool, args)

    db = RootDB(PROJECT_ROOT / "data/root.db")
    embedder = None

    try:
        # Only load embedder for tools that need it
        if tool in _EMBEDDING_TOOLS:
            embedder = _get_embedder()
            logger.info("embedder loaded in %.1fs", time.time() - start)

        if tool == "open_loops":
            from tools.correlations import open_loops
            scope = args[0] if args else "all"
            print(open_loops(db, embedder, scope=scope))

        elif tool == "themes":
            from tools.patterns import discover_themes
            scope = args[0] if args else "all"
            themes = discover_themes(db, embedder, scope=scope)
            for t in themes:
                print(f"## {t.get('theme_label', t.get('theme', t.get('label', 'Unknown')))}")
                for note in t.get("representative_notes", t.get("notes", t.get("members", [])))[:3]:
                    if isinstance(note, dict):
                        print(f"  - {note.get('title', note)}")
                    else:
                        print(f"  - {note}")
                print()

        elif tool == "blind_spots":
            from tools.graph import blind_spots
            print(blind_spots(db))

        elif tool == "weekly_digest":
            from tools.intelligence import weekly_digest
            print(weekly_digest(db))

        elif tool == "decision_trail":
            if not args:
                print("Usage: query.py decision_trail <topic>")
                sys.exit(1)
            from tools.graph import decision_trail
            print(decision_trail(" ".join(args), db, embedder))

        elif tool == "project_pulse":
            if not args:
                print("Usage: query.py project_pulse <project>")
                sys.exit(1)
            from tools.correlations import project_pulse
            print(project_pulse(" ".join(args), db, embedder))

        elif tool == "search":
            if not args:
                print("Usage: query.py search <query>")
                sys.exit(1)
            from tools.search import semantic_search
            print(semantic_search(" ".join(args), db, embedder))

        else:
            print(f"Unknown tool: {tool}")
            print("Available: open_loops, themes, blind_spots, weekly_digest, decision_trail, project_pulse, search")
            sys.exit(1)

        elapsed = time.time() - start
        logger.info("tool=%s completed in %.1fs", tool, elapsed)

    except Exception as e:
        elapsed = time.time() - start
        logger.error("tool=%s FAILED after %.1fs: %s", tool, elapsed, e, exc_info=True)
        print(f"# ROOT Error\n\n`{tool}` failed: {e}\n\nCheck stderr logs for details.")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
