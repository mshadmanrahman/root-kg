"""
ROOT CLI.

Setup wizard and management commands for the personal knowledge graph.
"""

import os
import shutil
import sys
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


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m root <command>")
        print("Commands: init, stats, index, extract")
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
    else:
        print(f"Unknown command: {command}")
        print("Commands: init, stats, index, extract")


if __name__ == "__main__":
    main()
