"""
ROOT embeddings layer.

Local embeddings via sentence-transformers. Zero API cost.
"""

import logging
import os

from sentence_transformers import SentenceTransformer

logger = logging.getLogger("root.embeddings")


class Embedder:
    """Local embedding model wrapper."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            self.model = SentenceTransformer(model_name)
        except (OSError, Exception) as e:
            logger.warning("Online model load failed (%s), trying local cache", e)
            os.environ["HF_HUB_OFFLINE"] = "1"
            self.model = SentenceTransformer(model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. More efficient than calling embed() in a loop."""
        embeddings = self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False,
        )
        return [e.tolist() for e in embeddings]
