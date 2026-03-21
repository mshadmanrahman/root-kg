"""
ROOT indexer.

Reads notes from configured sources, embeds them, and stores in the database.
Supports incremental indexing via content hashing.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from adapters.vault import scan_vault
from chunker import chunk_note
from db import RootDB
from embeddings import Embedder


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

    stats = {"scanned": 0, "new": 0, "updated": 0, "unchanged": 0, "errors": 0, "stale_removed": 0}

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

    # Remove notes that no longer exist in vault
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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ROOT indexer and entity extractor")
    parser.add_argument("--extract", action="store_true", help="Also run entity extraction after indexing")
    parser.add_argument("--extract-only", action="store_true", help="Skip indexing, only run entity extraction")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of notes to extract (for testing)")
    args = parser.parse_args()

    project_root = Path(__file__).parent
    config_path = project_root / "config.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    logger = _setup_logging(str(project_root / config.get("indexer", {}).get("log_dir", "logs")))
    logger.info("=" * 50)
    logger.info(f"ROOT indexer started at {datetime.now(timezone.utc).isoformat()}")

    db_path = project_root / config["database"]["path"]
    db = RootDB(db_path)

    try:
        if not args.extract_only:
            embedder = Embedder(config["embeddings"]["model"])
            logger.info(f"Model: {config['embeddings']['model']} ({embedder.dimension} dims)")
            stats = index_vault(config, db, embedder, logger)
            db_stats = db.get_stats()
            logger.info(f"Index total: {db_stats['total_notes']} notes, {db_stats['total_chunks']} chunks")

        if args.extract or args.extract_only:
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
