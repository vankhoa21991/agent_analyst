"""TF-IDF cosine search over a built index (see index.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .index import Chunk, _tokens


class Retriever:
    def __init__(self, index_path: str):
        payload = json.loads(Path(index_path).read_text(encoding="utf-8"))
        self._chunks = payload["chunks"]
        self._idf = payload["idf"]

    def search(self, query: str, k: int = 5) -> list[Chunk]:
        """Top-k chunks by TF-IDF cosine similarity (score > 0 only)."""
        qtokens = _tokens(query)
        if not qtokens or not self._chunks:
            return []
        qtf: dict[str, float] = {}
        for t in qtokens:
            qtf[t] = qtf.get(t, 0) + 1
        total = len(qtokens)
        qvec = {
            t: (c / total) * self._idf.get(t, 1.0) for t, c in qtf.items()
        }
        qnorm = math.sqrt(sum(v * v for v in qvec.values()))
        if qnorm == 0:
            return []

        scored: list[Chunk] = []
        for chunk in self._chunks:
            tf = chunk["tf"]
            dot = sum(
                qvec[t] * w * self._idf.get(t, 1.0)
                for t, w in tf.items()
                if t in qvec
            )
            if dot <= 0:
                continue
            cnorm = math.sqrt(
                sum(w * w * self._idf.get(t, 1.0) ** 2 for t, w in tf.items())
            )
            if cnorm == 0:
                continue
            scored.append(
                Chunk(text=chunk["text"], source=chunk["source"],
                      score=dot / (qnorm * cnorm))
            )
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]
