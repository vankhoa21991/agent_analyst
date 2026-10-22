"""In-process RAG: TF-IDF cosine retrieval over docs + schema chunks.

No Postgres, no embeddings model (see docs/adr/0002): dependency-free,
deterministic, fast enough for the small schemas in this project.
"""

from .index import Chunk, build_index
from .search import Retriever

__all__ = ["Chunk", "build_index", "Retriever"]
