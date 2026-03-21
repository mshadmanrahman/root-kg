"""
ROOT LLM layer.

Supports multiple backends for entity extraction and Q&A synthesis.
Default: Anthropic API (most reliable for structured extraction).
Fallback: OpenRouter (free $1 credit).
"""

import json
import os
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Load .env file if present (no dependency on python-dotenv)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


ENTITY_TYPES = ["person", "project", "decision", "event", "concept", "organization"]
RELATION_TYPES = [
    "works_with", "owns", "decided", "attended", "discussed",
    "blocked_by", "depends_on", "manages", "created", "reviewed",
]

EXTRACTION_SYSTEM = """You are an entity extraction assistant for a personal knowledge graph.
Extract entities and their relations from personal notes, meeting transcripts, and emails.

Rules:
- Only extract entities actually mentioned, not inferred
- Normalize names: first name for familiar people, full names for formal references
- For projects, use the canonical project name (e.g., "Heimdall" not "ad server replacement")
- Confidence: 0.9+ for explicit statements, 0.7 for implied, 0.5 for weak signals
- Context: quote the relevant phrase, max 200 characters
- Deduplicate: if the same entity appears multiple times, list it once with all aliases"""

# Tool schema for structured extraction (Anthropic format)
ANTHROPIC_EXTRACTION_TOOL = {
    "name": "store_extraction",
    "description": "Store extracted entities and relations from a note",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Canonical entity name"},
                        "type": {"type": "string", "enum": ENTITY_TYPES},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Alternative names/spellings",
                        },
                    },
                    "required": ["name", "type"],
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from_entity": {"type": "string", "description": "Source entity name"},
                        "relation": {"type": "string", "enum": RELATION_TYPES},
                        "to_entity": {"type": "string", "description": "Target entity name"},
                        "confidence": {"type": "number"},
                        "context": {"type": "string", "description": "Quote from the note (max 200 chars)"},
                    },
                    "required": ["from_entity", "relation", "to_entity", "confidence"],
                },
            },
        },
        "required": ["entities", "relations"],
    },
}

# OpenAI-compatible tool schema (for OpenRouter)
OPENAI_EXTRACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "store_extraction",
        "description": "Store extracted entities and relations from a note",
        "parameters": ANTHROPIC_EXTRACTION_TOOL["input_schema"],
    },
}

SYNTHESIS_SYSTEM = """You are ROOT, a personal knowledge assistant.
Answer based ONLY on the provided context from the user's knowledge graph.
Cite specific notes/sources when possible. Be direct and concise.
If the context doesn't contain enough information, say so clearly."""


def _http_post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    """Make an HTTP POST request and return JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e


class LLMClient:
    """Multi-backend LLM client for ROOT.

    Supports:
    - "anthropic": Direct Anthropic API (ANTHROPIC_API_KEY)
    - "openrouter": OpenRouter API (OPENROUTER_API_KEY)
    - "ollama": Local LLM via Ollama (free, no key needed)
    """

    def __init__(
        self,
        backend: str = "anthropic",
        extraction_model: Optional[str] = None,
        synthesis_model: Optional[str] = None,
    ):
        self.backend = backend

        if backend == "anthropic":
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            self.base_url = "https://api.anthropic.com/v1/messages"
            self.extraction_model = extraction_model or "claude-haiku-4-5-20251001"
            self.synthesis_model = synthesis_model or "claude-sonnet-4-20250514"
        elif backend == "openrouter":
            self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
            self.base_url = "https://openrouter.ai/api/v1/chat/completions"
            self.extraction_model = extraction_model or "anthropic/claude-haiku-4-5-20251001"
            self.synthesis_model = synthesis_model or "anthropic/claude-sonnet-4-20250514"
        elif backend == "ollama":
            self.api_key = ""
            self.base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
            self.extraction_model = extraction_model or "llama3.1"
            self.synthesis_model = synthesis_model or "llama3.1"
        else:
            raise ValueError(f"Unknown backend: {backend}. Use 'anthropic', 'openrouter', or 'ollama'.")

        if backend in ("anthropic", "openrouter") and not self.api_key:
            key_name = "ANTHROPIC_API_KEY" if backend == "anthropic" else "OPENROUTER_API_KEY"
            raise EnvironmentError(
                f"{key_name} not set. Add it to .env or your shell profile."
            )

    def extract_entities(self, title: str, content: str, max_chars: int = 6000) -> dict:
        """Extract entities and relations from a note.

        Returns dict with "entities" and "relations" lists.
        """
        truncated = content[:max_chars]
        user_msg = f"Note title: {title}\n\n{truncated}"

        if self.backend == "anthropic":
            return self._extract_anthropic(user_msg)
        elif self.backend == "ollama":
            return self._extract_ollama(user_msg)
        return self._extract_openrouter(user_msg)

    def synthesize(self, question: str, context: str) -> str:
        """Answer a question using provided context."""
        if self.backend == "anthropic":
            return self._synthesize_anthropic(question, context)
        elif self.backend == "ollama":
            return self._synthesize_ollama(question, context)
        return self._synthesize_openrouter(question, context)

    # ── Anthropic backend ────────────────────────────────────────

    def _extract_anthropic(self, user_msg: str) -> dict:
        payload = {
            "model": self.extraction_model,
            "max_tokens": 2048,
            "system": EXTRACTION_SYSTEM,
            "tools": [ANTHROPIC_EXTRACTION_TOOL],
            "tool_choice": {"type": "tool", "name": "store_extraction"},
            "messages": [{"role": "user", "content": user_msg}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        response = _http_post(self.base_url, headers, payload)

        for block in response.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "store_extraction":
                return block.get("input", {"entities": [], "relations": []})

        return {"entities": [], "relations": []}

    def _synthesize_anthropic(self, question: str, context: str) -> str:
        payload = {
            "model": self.synthesis_model,
            "max_tokens": 4096,
            "system": SYNTHESIS_SYSTEM,
            "messages": [{
                "role": "user",
                "content": f"Context from knowledge graph:\n\n{context}\n\n---\n\nQuestion: {question}",
            }],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        response = _http_post(self.base_url, headers, payload)
        content = response.get("content", [])
        if content:
            return content[0].get("text", "No response generated.")
        return "No response generated."

    # ── OpenRouter backend ───────────────────────────────────────

    def _extract_openrouter(self, user_msg: str) -> dict:
        payload = {
            "model": self.extraction_model,
            "max_tokens": 2048,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "tools": [OPENAI_EXTRACTION_TOOL],
            "tool_choice": {"type": "function", "function": {"name": "store_extraction"}},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        response = _http_post(self.base_url, headers, payload)

        choices = response.get("choices", [])
        if choices:
            tool_calls = choices[0].get("message", {}).get("tool_calls", [])
            for tc in tool_calls:
                if tc.get("function", {}).get("name") == "store_extraction":
                    args = tc["function"].get("arguments", "{}")
                    return json.loads(args) if isinstance(args, str) else args

        return {"entities": [], "relations": []}

    def _synthesize_openrouter(self, question: str, context: str) -> str:
        payload = {
            "model": self.synthesis_model,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {
                    "role": "user",
                    "content": f"Context from knowledge graph:\n\n{context}\n\n---\n\nQuestion: {question}",
                },
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        response = _http_post(self.base_url, headers, payload)
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "No response generated.")
        return "No response generated."

    # ── Ollama backend ───────────────────────────────────────────

    def _extract_ollama(self, user_msg: str) -> dict:
        """Ollama uses JSON mode instead of tool calling."""
        json_schema = json.dumps({
            "entities": [{"name": "string", "type": "person|project|decision|event|concept|organization", "aliases": ["string"]}],
            "relations": [{"from_entity": "string", "relation": "works_with|owns|decided|discussed|...", "to_entity": "string", "confidence": 0.9, "context": "string"}],
        }, indent=2)

        prompt = f"""{EXTRACTION_SYSTEM}

Return ONLY valid JSON in this exact format:
{json_schema}

{user_msg}"""

        payload = {
            "model": self.extraction_model,
            "messages": [{"role": "user", "content": prompt}],
            "format": "json",
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}

        try:
            response = _http_post(self.base_url, headers, payload, timeout=120)
            text = response.get("message", {}).get("content", "{}")
            result = json.loads(text)
            # Validate expected keys
            if "entities" in result and "relations" in result:
                return result
        except (json.JSONDecodeError, RuntimeError):
            pass

        return {"entities": [], "relations": []}

    def _synthesize_ollama(self, question: str, context: str) -> str:
        payload = {
            "model": self.synthesis_model,
            "messages": [
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {
                    "role": "user",
                    "content": f"Context from knowledge graph:\n\n{context}\n\n---\n\nQuestion: {question}",
                },
            ],
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}

        try:
            response = _http_post(self.base_url, headers, payload, timeout=120)
            return response.get("message", {}).get("content", "No response generated.")
        except RuntimeError as e:
            return f"Ollama error: {e}. Is Ollama running? (ollama serve)"
