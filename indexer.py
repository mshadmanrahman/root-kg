"""
ROOT indexer.

Reads notes from configured sources, embeds them, and stores in the database.
Supports incremental indexing via content hashing.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from adapters.vault import scan_vault
from chunker import chunk_note
from db import RootDB
from embeddings import Embedder


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


def index_vault(config: dict, db: RootDB, embedder: Embedder, logger: logging.Logger) -> dict:
    """Index all vault notes. Returns stats dict."""
    vault_config = config["vault"]
    now = datetime.now(timezone.utc).isoformat()

    stats = {
        "scanned": 0, "new": 0, "updated": 0, "unchanged": 0,
        "errors": 0, "stale_removed": 0, "skipped_thin": 0,
    }

    min_prose = config.get("indexer", {}).get("min_prose_chars", DEFAULT_MIN_PROSE_CHARS)

    # Collect all notes for batch embedding
    notes_to_embed = []
    all_paths = set()

    logger.info(f"Scanning vault: {vault_config['path']}")

    for note in scan_vault(
        vault_config["path"],
        exclude_folders=vault_config.get("exclude_folders"),
        exclude_patterns=vault_config.get("exclude_patterns"),
    ):
        stats["scanned"] += 1

        # Navigation-only notes are excluded from the index. Deliberately left
        # out of all_paths, so any already indexed get dropped by the stale
        # sweep below.
        if min_prose and prose_length(note["content"]) < min_prose:
            stats["skipped_thin"] += 1
            continue

        all_paths.add(note["path"])

        # Check if content changed
        stored_hash = db.get_note_hash(note["path"])
        if stored_hash == note["content_hash"]:
            stats["unchanged"] += 1
            continue

        if stored_hash is None:
            stats["new"] += 1
        else:
            stats["updated"] += 1

        notes_to_embed.append({**note, "indexed_at": now})

    # Safety guard: if scan returned 0 results but DB has vault notes, abort
    # This prevents accidental purge when vault path is inaccessible
    if stats["scanned"] == 0:
        existing_vault_count = db.count_notes_by_source("vault")
        if existing_vault_count > 0:
            logger.error(
                f"SAFETY ABORT: Vault scan returned 0 notes but DB has {existing_vault_count} vault notes. "
                f"Vault path may be inaccessible. Skipping stale removal to prevent data loss."
            )
            stats["stale_removed"] = 0
            return stats

    # Circuit breaker: a threshold that rejects a quarter of the vault is
    # misconfigured, not a discovery. Keep the existing index rather than let an
    # unattended run purge it.
    if stats["scanned"] and stats["skipped_thin"] > THIN_SKIP_ABORT_FRACTION * stats["scanned"]:
        logger.error(
            f"SAFETY ABORT: min_prose_chars={min_prose} skipped {stats['skipped_thin']} of "
            f"{stats['scanned']} notes ({stats['skipped_thin'] / stats['scanned']:.0%}). "
            f"Threshold looks wrong. Skipping stale removal to prevent data loss."
        )
        return stats

    if stats["skipped_thin"]:
        logger.info(
            f"Skipped {stats['skipped_thin']} navigation-only notes "
            f"(under {min_prose} chars of prose)."
        )

    # Remove notes that no longer exist in vault, plus any now-skipped thin notes
    stats["stale_removed"] = db.remove_stale_notes(all_paths)

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
        chunks = chunk_note(note["content"], note["title"])
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
                source_type="vault",
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
    from extractor import extract_all
    from llm import LLMClient

    llm_config = config.get("llm", {})
    llm = LLMClient(
        backend=llm_config.get("backend", "anthropic"),
        extraction_model=llm_config.get("extraction_model"),
        synthesis_model=llm_config.get("synthesis_model"),
    )
    batch_delay = llm_config.get("batch_delay_ms", 100)

    stats = extract_all(db, llm, logger, limit=limit, batch_delay_ms=batch_delay)
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

    project_root = Path(__file__).parent
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
