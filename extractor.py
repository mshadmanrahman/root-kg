"""
ROOT entity extraction pipeline.

Incremental, hash-tracked extraction of entities and relations from indexed notes.
Uses LLM (Anthropic Haiku) for accurate extraction from personal knowledge.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from db import RootDB
from llm import LLMClient


# Generic phrases the extraction LLM sometimes emits as "aliases" but which are
# descriptions, not names. Accepting them is how an entity becomes a magnet
# super-node: resolve_entity() routes future mentions by alias, so a generic
# alias silently re-attaches every unrelated mention onto one entity. Observed
# in practice: one project entity absorbed generic phrases like "system", "the
# product", and "control plane" and ballooned into the largest node in the graph.
# These never pass.
_GENERIC_ALIAS_STOPWORDS = frozenset({
    "system", "the system", "platform", "the platform", "the product",
    "operating system", "operating layer", "control plane", "command plane",
    "command and control plane", "nervous system", "agent runtime system",
    "agent runtime", "agent execution runtime", "task management system",
    "issue tracking system", "activity log", "mutation recording system",
    "memory service", "runtime", "orchestrator", "backend", "frontend",
    "api", "ui", "cli", "sdk", "os", "ceo", "cto", "app", "the app",
    "the tool", "the service", "the repo", "the codebase", "the project",
    "the company",
})
_ALIAS_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALIAS_STOP_TOKENS = frozenset({"the", "a", "an", "of", "for", "and", "ai", "to", "in", "on"})


def _is_low_quality_alias(alias: str, entity_name: str) -> bool:
    """True if `alias` is too generic to be a safe alias for `entity_name`.

    Aliases drive resolve_entity(), so a generic phrase accepted as an alias
    re-routes unrelated mentions onto one entity (magnet super-node). We reject
    curated generics, leading-article descriptors, and (when the alias shares no
    real token with the canonical name) anything that is not a tight identifier
    (acronym or handle). Real name variants keep a shared token, a substring of
    the name, or an acronym/handle shape; English descriptions do not.
    """
    norm = alias.strip().lower()
    if not norm or norm in _GENERIC_ALIAS_STOPWORDS:
        return True
    if norm.split()[0] in {"the", "a", "an"}:
        return True
    name_norm = entity_name.lower()
    alias_tokens = {t for t in _ALIAS_TOKEN_RE.findall(norm) if t not in _ALIAS_STOP_TOKENS}
    name_tokens = {t for t in _ALIAS_TOKEN_RE.findall(name_norm) if t not in _ALIAS_STOP_TOKENS}
    # Genuine variant: shares a real token, or one name is a substring of the
    # other (e.g. "acmecorp" / "app.acme.io" vs "Acme").
    if (alias_tokens & name_tokens) or norm in name_norm or name_norm in norm:
        return False
    # No overlap with the name. Keep only tight identifiers (single-token
    # acronyms or handles); reject multi-word English descriptions.
    is_identifier = len(norm.split()) == 1 and (
        alias.strip().isupper() or any(c in alias for c in "@/")
    )
    return not is_identifier


@dataclass(frozen=True)
class ExtractionStats:
    """Immutable extraction run statistics."""
    processed: int = 0
    entities_found: int = 0
    relations_found: int = 0
    skipped: int = 0
    errors: int = 0
    error_paths: tuple[str, ...] = field(default_factory=tuple)


def extract_all(
    db: RootDB,
    llm: LLMClient,
    logger: logging.Logger,
    limit: Optional[int] = None,
    batch_delay_ms: int = 100,
) -> ExtractionStats:
    """Extract entities from all notes needing extraction.

    Incremental: only processes notes whose content_hash changed since last extraction.
    Returns immutable stats object.
    """
    notes = db.get_notes_needing_extraction()
    if limit:
        notes = notes[:limit]

    if not notes:
        logger.info("No notes need extraction")
        return ExtractionStats()

    logger.info(f"Extracting entities from {len(notes)} notes")

    processed = 0
    entities_found = 0
    relations_found = 0
    errors = 0
    error_paths = []

    for i, note in enumerate(notes):
        try:
            result = _extract_note(db, llm, note, logger)
            processed += 1
            entities_found += result["entity_count"]
            relations_found += result["relation_count"]

            if (i + 1) % 10 == 0:
                logger.info(f"  Progress: {i + 1}/{len(notes)} notes processed")

            # Rate limiting between API calls
            if batch_delay_ms > 0 and i < len(notes) - 1:
                time.sleep(batch_delay_ms / 1000.0)

        except Exception as e:
            errors += 1
            error_paths.append(note["path"])
            logger.error(f"  Extraction failed for {note['path']}: {e}")

    logger.info(
        f"Extraction complete: {processed} processed, "
        f"{entities_found} entities, {relations_found} relations, "
        f"{errors} errors"
    )

    return ExtractionStats(
        processed=processed,
        entities_found=entities_found,
        relations_found=relations_found,
        skipped=len(notes) - processed - errors,
        errors=errors,
        error_paths=tuple(error_paths),
    )


def _extract_note(
    db: RootDB,
    llm: LLMClient,
    note: dict,
    logger: logging.Logger,
) -> dict:
    """Extract entities and relations from a single note.

    Returns dict with entity_count and relation_count.
    """
    note_id = note["id"]
    title = note["title"]
    content = note["content"]
    content_hash = note["content_hash"]

    # Clear previous extraction data for this note (idempotent re-extraction)
    db.clear_extraction_for_note(note_id)

    # Call LLM for extraction
    result = llm.extract_entities(title, content)

    # Store entities
    entity_ids = {}
    for ent in result.get("entities", []):
        if not isinstance(ent, dict):
            continue
        ent_name = ent.get("name", "")
        if not isinstance(ent_name, str):
            continue
        ent_name = ent_name.strip()
        ent_type = ent.get("type", "concept")
        if not ent_name:
            continue

        eid = db.upsert_entity(
            name=ent_name,
            entity_type=ent_type,
            first_seen=note.get("created_at"),
            last_seen=note.get("indexed_at"),
        )
        entity_ids[ent_name] = eid
        db.link_entity_to_note(eid, note_id)

        for alias in ent.get("aliases", []):
            alias = alias.strip()
            if alias and alias != ent_name and not _is_low_quality_alias(alias, ent_name):
                db.add_alias(eid, alias)

    # Store relations
    relation_count = 0
    for rel in result.get("relations", []):
        if not isinstance(rel, dict):
            continue
        from_name = (rel.get("from_entity") or "").strip()
        to_name = (rel.get("to_entity") or "").strip()
        rel_type = rel.get("relation", "discussed")

        a_id = entity_ids.get(from_name) or db.resolve_entity(from_name)
        b_id = entity_ids.get(to_name) or db.resolve_entity(to_name)

        if a_id and b_id and a_id != b_id:
            db.upsert_relation(
                entity_a_id=a_id,
                relation_type=rel_type,
                entity_b_id=b_id,
                source_note_id=note_id,
                confidence=rel.get("confidence", 0.8),
                context=rel.get("context", "")[:200],
            )
            relation_count += 1

    # Mark as extracted
    db.mark_extracted(note_id, content_hash, llm.extraction_model)

    logger.debug(
        f"  Extracted: {title} -> {len(entity_ids)} entities, {relation_count} relations"
    )

    return {
        "entity_count": len(entity_ids),
        "relation_count": relation_count,
    }
