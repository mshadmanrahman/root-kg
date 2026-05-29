"""
ROOT CLI.

Setup wizard, management commands, and cron-callable search/ingest
for the personal knowledge graph.

Usage (cron-safe):
    python cli.py search --query "morning digest signals" [--limit 5]
    python cli.py note --content "Musa pulse: ..." [--tags "musa,signal"]

Exit codes: 0 = success, 1 = error. Errors go to stderr, results to stdout.
"""

import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIG_EXAMPLE = PROJECT_ROOT / "config.example.yaml"
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


def init():
    """Interactive setup wizard for ROOT."""
    print("=" * 50)
    print("  ROOT: Personal Knowledge Graph Setup")
    print("=" * 50)
    print()

    # Step 1: Vault path
    vault_path = _ask_vault_path()

    # Step 2: LLM backend
    backend, api_key = _ask_llm_backend()

    # Step 3: Create config.yaml
    _create_config(vault_path, backend)

    # Step 4: Create .env
    _create_env(backend, api_key)

    # Step 5: Create directories
    (PROJECT_ROOT / "data").mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    print()
    print("=" * 50)
    print("  Setup complete!")
    print("=" * 50)
    print()
    print("Next steps:")
    print("  1. Index your vault:")
    print("     python indexer.py")
    print()
    print("  2. Extract entities (requires LLM):")
    print("     python indexer.py --extract")
    print()
    print("  3. Register as MCP server in Claude Code:")
    print('     claude mcp add root -- python server.py')
    print()
    print("  4. Try it:")
    print('     root_search("your topic")')
    print('     root_ask("your question")')
    print()


def _ask_vault_path() -> str:
    """Ask for the vault/notes directory."""
    print("Where are your notes?")
    print()

    # Try to detect common vault locations
    home = Path.home()
    candidates = [
        home / "Library/Mobile Documents/iCloud~md~obsidian/Documents",
        home / "Documents",
        home / "Obsidian",
        home / "Notes",
    ]

    detected = []
    for candidate in candidates:
        if candidate.exists():
            # Look for folders with .md files
            for folder in candidate.iterdir():
                if folder.is_dir() and not folder.name.startswith("."):
                    md_count = len(list(folder.glob("**/*.md")))
                    if md_count > 10:
                        detected.append((folder, md_count))

    if detected:
        print("Detected vaults:")
        for i, (path, count) in enumerate(detected[:5], 1):
            print(f"  {i}. {path} ({count} notes)")
        print(f"  {len(detected[:5]) + 1}. Enter custom path")
        print()

        choice = input("Choose [1]: ").strip() or "1"
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(detected[:5]):
                return str(detected[idx][0])
        except ValueError:
            pass

    # Custom path
    while True:
        path = input("Enter path to your notes folder: ").strip()
        path = os.path.expanduser(path)
        if Path(path).exists():
            md_count = len(list(Path(path).glob("**/*.md")))
            print(f"  Found {md_count} markdown files.")
            if md_count > 0:
                return path
            print("  No markdown files found. Try a different path.")
        else:
            print("  Path doesn't exist. Try again.")


def _ask_llm_backend() -> tuple[str, str]:
    """Ask which LLM backend to use."""
    print()
    print("Which LLM backend for entity extraction?")
    print("  1. Anthropic API (~$3 for full extraction, best quality)")
    print("  2. OpenRouter (free $1 credit)")
    print("  3. Ollama (free, local, lower quality)")
    print("  4. Skip (no extraction, search-only mode)")
    print()

    choice = input("Choose [1]: ").strip() or "1"

    if choice == "1":
        key = input("Anthropic API key (or press Enter to add later): ").strip()
        return "anthropic", key
    elif choice == "2":
        key = input("OpenRouter API key (or press Enter to add later): ").strip()
        return "openrouter", key
    elif choice == "3":
        print("  Make sure Ollama is running: ollama serve")
        print("  And pull a model: ollama pull llama3.1")
        return "ollama", ""
    else:
        return "anthropic", ""


def _create_config(vault_path: str, backend: str) -> None:
    """Create config.yaml from template."""
    if CONFIG_PATH.exists():
        overwrite = input(f"\nconfig.yaml already exists. Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("  Keeping existing config.yaml")
            return

    with open(CONFIG_EXAMPLE) as f:
        config = yaml.safe_load(f)

    config["vault"]["path"] = vault_path
    config["llm"]["backend"] = backend

    # Set model names based on backend
    if backend == "openrouter":
        config["llm"]["extraction_model"] = "anthropic/claude-haiku-4-5-20251001"
        config["llm"]["synthesis_model"] = "anthropic/claude-sonnet-4-20250514"
    elif backend == "ollama":
        config["llm"]["extraction_model"] = "llama3.1"
        config["llm"]["synthesis_model"] = "llama3.1"

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"  Created config.yaml")


def _create_env(backend: str, api_key: str) -> None:
    """Create .env file with API key."""
    if not api_key:
        if not ENV_PATH.exists():
            shutil.copy(ENV_EXAMPLE, ENV_PATH)
            print(f"  Created .env from template (add your API key later)")
        return

    key_name = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(backend)

    if key_name:
        with open(ENV_PATH, "w") as f:
            f.write(f"{key_name}={api_key}\n")
        print(f"  Created .env with {key_name}")


def stats():
    """Show ROOT index and graph statistics."""
    from db import RootDB
    db = RootDB(PROJECT_ROOT / "data/root.db")

    index_stats = db.get_stats()
    entity_stats = db.get_entity_stats()

    print("ROOT Stats")
    print(f"  Notes:      {index_stats['total_notes']}")
    print(f"  Chunks:     {index_stats['total_chunks']}")
    print(f"  Entities:   {entity_stats['total_entities']}")
    print(f"  Relations:  {entity_stats['total_relations']}")
    print(f"  Extracted:  {entity_stats['notes_extracted']}/{index_stats['total_notes']}")
    print(f"  Last index: {index_stats['last_indexed']}")

    if entity_stats["by_entity_type"]:
        print("\n  Entity breakdown:")
        for etype, count in entity_stats["by_entity_type"].items():
            print(f"    {etype}: {count}")

    db.close()


def _load_config() -> dict:
    """Load config.yaml. Exits with error if missing."""
    if not CONFIG_PATH.exists():
        print("Error: config.yaml not found. Run: python cli.py init", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def search(query: str, limit: int = 5) -> None:
    """Semantic search across ROOT's knowledge graph.

    Prints one result per line: "<rank>. [<folder>] <title> -- <snippet>"
    Suitable for capturing into a bash variable.
    """
    config = _load_config()
    db_path = PROJECT_ROOT / config["database"]["path"]

    from db import RootDB
    from embeddings import Embedder
    from tools.search import semantic_search

    try:
        db = RootDB(db_path)
        embedder = Embedder(config["embeddings"]["model"])
        results = semantic_search(query=query, db=db, embedder=embedder, limit=limit)
        db.close()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not results:
        # Empty stdout, exit 0 -- caller treats empty output as "no results"
        return

    for i, r in enumerate(results, 1):
        snippet = r["snippet"].replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        print(f"{i}. [{r['folder']}] {r['title']} -- {snippet}")


def note(content: str, tags: list[str] | None = None) -> None:
    """Ingest a plain-text note into ROOT's index.

    Uses the same chunking + embedding pipeline as root_ingest (server.py).
    source_type is 'cli' so notes are queryable via root_search without
    polluting the vault source bucket.
    """
    config = _load_config()
    db_path = PROJECT_ROOT / config["database"]["path"]

    from chunker import chunk_note
    from db import RootDB
    from embeddings import Embedder

    now = datetime.now(timezone.utc).isoformat()

    # Derive a short title from the first non-empty line
    first_line = next((ln.strip() for ln in content.splitlines() if ln.strip()), "CLI Note")
    title = first_line[:120]

    # Stable unique path: hash of content + ingest timestamp to avoid collisions
    # on identical content ingested at different times
    path_hash = hashlib.sha256((content + now).encode()).hexdigest()[:16]
    path = f"cli-notes/{path_hash}"

    tag_str = (",".join(tags) + " ") if tags else ""
    # Prepend tags into body so they're searchable
    indexed_content = f"{tag_str}{content}" if tag_str else content
    content_hash = hashlib.sha256(indexed_content.encode("utf-8")).hexdigest()
    folder = "CLI Notes"

    try:
        db = RootDB(db_path)
        embedder = Embedder(config["embeddings"]["model"])

        note_id = db.upsert_note(
            path=path,
            title=title,
            content=indexed_content,
            content_hash=content_hash,
            folder=folder,
            source_type="cli",
            created_at=now,
            indexed_at=now,
        )

        chunks = chunk_note(indexed_content, title)
        if chunks:
            texts = [c["text"] for c in chunks]
            embeddings = embedder.embed_batch(texts)
            indexed_chunks = [
                {"idx": c["idx"], "text": c["text"], "embedding": emb}
                for c, emb in zip(chunks, embeddings)
            ]
            db.store_chunks(note_id, indexed_chunks)

        db.close()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Success: silent stdout, exit 0


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python cli.py <command>")
        print("Commands: init, stats, index, extract, search, note")
        return

    command = sys.argv[1]

    if command == "init":
        init()
    elif command == "stats":
        stats()
    elif command == "index":
        from indexer import main as index_main
        sys.argv = sys.argv[1:]  # Shift args for indexer's argparse
        index_main()
    elif command == "extract":
        sys.argv = ["indexer", "--extract-only"] + sys.argv[2:]
        from indexer import main as index_main
        index_main()
    elif command == "search":
        import argparse
        parser = argparse.ArgumentParser(prog="cli.py search")
        parser.add_argument("--query", required=True, help="Natural language search query")
        parser.add_argument("--limit", type=int, default=5, help="Max results (default 5)")
        args = parser.parse_args(sys.argv[2:])
        search(query=args.query, limit=args.limit)
    elif command == "note":
        import argparse
        parser = argparse.ArgumentParser(prog="cli.py note")
        parser.add_argument("--content", required=True, help="Plain text note content")
        parser.add_argument("--tags", default="", help="Comma-separated tags (optional)")
        args = parser.parse_args(sys.argv[2:])
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        note(content=args.content, tags=tags)
    else:
        print(f"Unknown command: {command}")
        print("Commands: init, stats, index, extract, search, note")


if __name__ == "__main__":
    main()
