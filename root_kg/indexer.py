"""
ROOT indexer.

Reads notes from configured sources, embeds them, and stores in the database.
Supports incremental indexing via content hashing.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from root_kg.adapters.vault import scan_vault
from root_kg.paths import PROJECT_ROOT
from root_kg.chunker import chunk_note, chunk_size
from root_kg.db import RootDB
from root_kg.embeddings import Embedder


# A note whose body is only navigation (frontmatter tags, a title, a file embed,
# prev/next wikilinks) carries no answerable prose. Indexing these hurts search:
# their embeddings collapse into near-identical tag soup and crowd out real
# answers for any query using the same vocabulary. Observed on a 2,600-note
# vault: ~40 lecture stub notes, each a title plus a PDF embed, occupied the
# entire top 10 for any query about that subject while answering nothing,
# because their real content lived in PDFs the indexer cannot read.
DEFAULT_MIN_PROSE_CHARS = 30

# Fraction of scanned notes that, if skipped as thin, means the threshold is
# almost certainly misconfigured. Aborts stale removal rather than purging the
# index, since this script typically runs unattended on a timer. A correct
# threshold excludes a few percent; 10% leaves ample headroom.
THIN_SKIP_ABORT_FRACTION = 0.10


def prose_length(content: str) -> int:
    """Length of a note's original prose, excluding navigation and metadata.

    Strips YAML frontmatter, embeds, wikilinks, markdown links, headings, list
    markers, table pipes and rules, then measures what remains. A note that
    only points at other things scores near zero; a note with real text does
    not. Wikilink display text is dropped deliberately: a link is navigation,
    not prose, even when its label is descriptive.
    """
    text = re.sub(r"^---\n.*?\n---", "", content, count=1, flags=re.DOTALL)
    text = re.sub(r"!\[\[[^\]]*\]\]", "", text)           # embeds: ![[file.pdf]]
    text = re.sub(r"\[\[[^\]]*\]\]", "", text)            # wikilinks
    text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text)     # markdown links/images
    text = re.sub(r"^\s{0,3}#{1,6}\s.*$", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)         # bullets
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)         # ordered
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)    # rules
    text = re.sub(r"[|>`*_~#]", " ", text)                # table pipes, quotes, emphasis
    return len(" ".join(text.split()))


def _setup_logging(log_dir: str) -> logging.Logger:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("root-indexer")
    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(log_path / "indexer.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


def configured_roots(config: dict) -> list[dict]:
    """Normalise vault config into a list of roots.

    Back-compatible: a scalar `vault.path` stays one root with source_type
    "vault", no path prefix, extraction on. `vault.roots` adds more. Each root
    carries its own source_type, so stale sweeps, stats and search filters stay
    independent, and its own `extract` flag, so indexing a root does not commit
    it to LLM entity extraction.
    """
    vault_config = config["vault"]
    roots: list[dict] = []

    if vault_config.get("path"):
        roots.append({
            "path": os.path.expanduser(vault_config["path"]),
            "source_type": "vault",
            "prefix": "",
            "extract": vault_config.get("extract", True),
            "exclude_folders": vault_config.get("exclude_folders"),
            "exclude_patterns": vault_config.get("exclude_patterns"),
        })

    for entry in vault_config.get("roots") or []:
        source_type = entry.get("source_type") or entry["name"]
        roots.append({
            "path": os.path.expanduser(entry["path"]),
            "source_type": source_type,
            # Paths are UNIQUE in `notes`, and two roots can both hold
            # `index.md`. The prefix keeps them distinct. The primary vault root
            # keeps an empty prefix so an existing index is not orphaned and
            # re-embedded when you add a second root.
            "prefix": entry.get("prefix", source_type),
            "extract": entry.get("extract", False),
            "exclude_folders": entry.get("exclude_folders"),
            "exclude_patterns": entry.get("exclude_patterns"),
        })

    return roots


def extraction_source_types(config: dict) -> list[str]:
    """Source types whose notes may be sent to the LLM for entity extraction."""
    return [r["source_type"] for r in configured_roots(config) if r["extract"]]


def index_vault(config: dict, db: RootDB, embedder: Embedder, logger: logging.Logger) -> dict:
    """Index every configured root. Returns stats dict."""
    now = datetime.now(timezone.utc).isoformat()

    stats = {
        "scanned": 0, "new": 0, "updated": 0, "unchanged": 0,
        "errors": 0, "stale_removed": 0, "skipped_thin": 0,
    }

    min_prose = config.get("indexer", {}).get("min_prose_chars", DEFAULT_MIN_PROSE_CHARS)
    max_chars = chunk_size(config)

    # Collect all notes for batch embedding
    notes_to_embed = []

    for root in configured_roots(config):
        source_type = root["source_type"]
        prefix = root["prefix"]
        root_stats = {"scanned": 0, "skipped_thin": 0}
        root_paths: set[str] = set()

        logger.info(f"Scanning {source_type}: {root['path']}")

        try:
            for note in scan_vault(
                root["path"],
                exclude_folders=root["exclude_folders"],
                exclude_patterns=root["exclude_patterns"],
            ):
                root_stats["scanned"] += 1
                path = f"{prefix}/{note['path']}" if prefix else note["path"]

                # Navigation-only notes are excluded from the index.
                # Deliberately left out of root_paths, so any already indexed
                # get dropped by the stale sweep below.
                if min_prose and prose_length(note["content"]) < min_prose:
                    root_stats["skipped_thin"] += 1
                    continue

                root_paths.add(path)

                # Check if content changed
                stored_hash = db.get_note_hash(path)
                if stored_hash == note["content_hash"]:
                    stats["unchanged"] += 1
                    continue

                if stored_hash is None:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1

                notes_to_embed.append({
                    **note,
                    "path": path,
                    "source_type": source_type,
                    "indexed_at": now,
                })
        except (FileNotFoundError, OSError) as e:
            # An unreachable root must not take the run down, and must not let
            # its own notes be swept. Skip its sweep and carry on.
            logger.error(
                f"SAFETY SKIP: root '{source_type}' unreadable ({e}). Leaving its notes untouched."
            )
            stats["errors"] += 1
            continue

        stats["scanned"] += root_stats["scanned"]
        stats["skipped_thin"] += root_stats["skipped_thin"]

        # Safety guard, per root: a scan returning nothing where the DB holds
        # notes means the path is inaccessible, not that the notes are gone.
        if root_stats["scanned"] == 0:
            existing = db.count_notes_by_source(source_type)
            if existing > 0:
                logger.error(
                    f"SAFETY ABORT: {source_type} scan returned 0 notes but DB has {existing}. "
                    f"Path may be inaccessible. Skipping stale removal for this root."
                )
                continue

        # Circuit breaker, per root: a threshold that rejects a quarter of a
        # root is misconfigured, not a discovery. Keep the existing index rather
        # than let an unattended run purge it.
        if root_stats["scanned"] and root_stats["skipped_thin"] > THIN_SKIP_ABORT_FRACTION * root_stats["scanned"]:
            logger.error(
                f"SAFETY ABORT: min_prose_chars={min_prose} skipped {root_stats['skipped_thin']} of "
                f"{root_stats['scanned']} {source_type} notes "
                f"({root_stats['skipped_thin'] / root_stats['scanned']:.0%}). "
                f"Threshold looks wrong. Skipping stale removal for this root."
            )
            continue

        if root_stats["skipped_thin"]:
            logger.info(
                f"Skipped {root_stats['skipped_thin']} navigation-only {source_type} notes "
                f"(under {min_prose} chars of prose)."
            )

        # Remove notes that no longer exist in this root, plus any now-skipped thin notes
        stats["stale_removed"] += db.remove_stale_notes(root_paths, source_type=source_type)

    if not notes_to_embed:
        logger.info(f"No changes detected. {stats['scanned']} notes scanned, all up to date.")
        if stats["stale_removed"]:
            logger.info(f"Removed {stats['stale_removed']} stale notes.")
        return stats

    logger.info(f"Embedding {len(notes_to_embed)} notes ({stats['new']} new, {stats['updated']} updated)...")

    # Chunk all notes
    all_chunks = []
    chunk_map = []  # Track which chunks belong to which note

    for note in notes_to_embed:
        chunks = chunk_note(note["content"], note["title"], max_chars=max_chars)
        chunk_map.append({"note": note, "chunk_count": len(chunks)})
        all_chunks.extend(chunks)

    # Batch embed all chunks
    chunk_texts = [c["text"] for c in all_chunks]
    batch_size = config.get("indexer", {}).get("batch_size", 64)

    all_embeddings = []
    for i in range(0, len(chunk_texts), batch_size):
        batch = chunk_texts[i : i + batch_size]
        batch_embeddings = embedder.embed_batch(batch)
        all_embeddings.extend(batch_embeddings)
        if len(chunk_texts) > batch_size:
            logger.info(f"  Embedded {min(i + batch_size, len(chunk_texts))}/{len(chunk_texts)} chunks...")

    # Store notes and chunks
    embed_idx = 0
    for entry in chunk_map:
        note = entry["note"]
        chunk_count = entry["chunk_count"]

        try:
            note_id = db.upsert_note(
                path=note["path"],
                title=note["title"],
                content=note["content"],
                content_hash=note["content_hash"],
                folder=note["folder"],
                source_type=note.get("source_type", "vault"),
                created_at=note.get("created_at"),
                indexed_at=note["indexed_at"],
            )

            note_chunks = []
            for j in range(chunk_count):
                note_chunks.append({
                    "idx": j,
                    "text": chunk_texts[embed_idx],
                    "embedding": all_embeddings[embed_idx],
                })
                embed_idx += 1

            db.store_chunks(note_id, note_chunks)

        except Exception as e:
            logger.error(f"Error indexing {note['path']}: {e}")
            stats["errors"] += 1
            embed_idx += chunk_count  # Skip these embeddings

    logger.info(
        f"Done. {stats['new']} new, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged, {stats['stale_removed']} removed, "
        f"{stats['errors']} errors."
    )
    return stats


def run_extraction(config: dict, db: RootDB, logger: logging.Logger, limit: int | None = None) -> dict:
    """Run entity extraction on notes that need it. Returns stats dict."""
    from root_kg.extractor import extract_all
    from root_kg.llm import LLMClient

    llm_config = config.get("llm", {})
    llm = LLMClient(
        backend=llm_config.get("backend", "anthropic"),
        extraction_model=llm_config.get("extraction_model"),
        synthesis_model=llm_config.get("synthesis_model"),
    )
    batch_delay = llm_config.get("batch_delay_ms", 100)

    # Only roots with extract: true reach the LLM. Indexing a root is free
    # (local embeddings); extracting it is not, so the two are separate choices.
    source_types = extraction_source_types(config)
    logger.info(f"Extraction sources: {', '.join(source_types) or '(none)'}")

    stats = extract_all(
        db, llm, logger, limit=limit, batch_delay_ms=batch_delay,
        source_types=source_types,
    )
    logger.info(
        f"Extraction: {stats.processed} processed, {stats.entities_found} entities, "
        f"{stats.relations_found} relations, {stats.errors} errors"
    )
    return {
        "processed": stats.processed,
        "entities": stats.entities_found,
        "relations": stats.relations_found,
        "errors": stats.errors,
    }


def report_thin(config: dict) -> None:
    """Print the notes that would be excluded as navigation-only, and exit.

    Read-only. Use this to tune min_prose_chars before letting the indexer act
    on it, since excluded notes are dropped from the index.
    """
    vault_config = config["vault"]
    min_prose = config.get("indexer", {}).get("min_prose_chars", DEFAULT_MIN_PROSE_CHARS)

    thin, kept, by_folder = [], 0, {}
    for note in scan_vault(
        vault_config["path"],
        exclude_folders=vault_config.get("exclude_folders"),
        exclude_patterns=vault_config.get("exclude_patterns"),
    ):
        length = prose_length(note["content"])
        if length < min_prose:
            thin.append((length, note["path"]))
            top = note["path"].split("/")[0]
            by_folder[top] = by_folder.get(top, 0) + 1
        else:
            kept += 1

    total = len(thin) + kept
    if not total:
        print("No notes scanned. Check vault.path in config.yaml.")
        return

    print(f"min_prose_chars = {min_prose}")
    print(f"would exclude {len(thin)} of {total} notes ({len(thin) / total:.1%}), keeping {kept}\n")
    print("by top-level folder:")
    for folder, count in sorted(by_folder.items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {folder}")
    print("\nclosest to the threshold (review these first):")
    for length, path in sorted(thin, reverse=True)[:15]:
        print(f"  {length:5d}  {path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ROOT indexer and entity extractor")
    parser.add_argument("--extract", action="store_true", help="Also run entity extraction after indexing")
    parser.add_argument("--extract-only", action="store_true", help="Skip indexing, only run entity extraction")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of notes to extract (for testing)")
    parser.add_argument(
        "--report-thin",
        action="store_true",
        help="List the navigation-only notes that would be excluded, then exit without touching the index",
    )
    parser.add_argument(
        "--min-prose",
        type=int,
        default=None,
        help="Override indexer.min_prose_chars for this run (use with --report-thin to tune)",
    )
    args = parser.parse_args()

    project_root = PROJECT_ROOT
    config_path = project_root / "config.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    if args.min_prose is not None:
        config.setdefault("indexer", {})["min_prose_chars"] = args.min_prose

    if args.report_thin:
        report_thin(config)
        return

    logger = _setup_logging(str(project_root / config.get("indexer", {}).get("log_dir", "logs")))
    logger.info("=" * 50)
    logger.info(f"ROOT indexer started at {datetime.now(timezone.utc).isoformat()}")

    db_path = project_root / config["database"]["path"]
    db = RootDB(db_path)

    def _llm_paused() -> bool:
        """Check the shared LLM quota guard. Auto-clears if reset_epoch has passed."""
        import time as _time
        guard = project_root / ".llm-paused"
        if not guard.exists():
            return False
        try:
            for line in guard.read_text().splitlines():
                if line.startswith("reset_epoch:"):
                    epoch = int(line.split(":", 1)[1].strip())
                    if _time.time() >= epoch:
                        guard.unlink(missing_ok=True)
                        return False
        except Exception:
            pass
        return True

    try:
        if not args.extract_only:
            embedder = Embedder(config["embeddings"]["model"])
            logger.info(f"Model: {config['embeddings']['model']} ({embedder.dimension} dims)")
            stats = index_vault(config, db, embedder, logger)
            db_stats = db.get_stats()
            logger.info(f"Index total: {db_stats['total_notes']} notes, {db_stats['total_chunks']} chunks")

        if args.extract or args.extract_only:
            if _llm_paused():
                logger.info("SKIP: entity extraction paused by LLM quota guard.")
            else:
                logger.info("Starting entity extraction...")
                run_extraction(config, db, logger, limit=args.limit)
                entity_stats = db.get_entity_stats()
                logger.info(
                    f"Graph total: {entity_stats['total_entities']} entities, "
                    f"{entity_stats['total_relations']} relations"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
