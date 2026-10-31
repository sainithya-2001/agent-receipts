"""Find the rules relevant to a dispute: BM25 over the rules from ingest.py.

Plain lexical search, deterministic and with no extra service. It is enough for a corpus of
a few dozen rules; pgvector embeddings can replace it once Postgres is in (Day 11).
"""
import math
import re
from collections import Counter
from functools import lru_cache

from backend.rag.ingest import load_rules

K1, B = 1.5, 0.75
_STOP = set("a an and are as at be by for from has if in is it its not of on or so that the their them they this to "
            "was we with what when which who will you your".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in _STOP]


@lru_cache
def _index():
    docs = [tokens(f"{r['id']} {r['title']} {r['title']} {r['text']}") for r in load_rules()]
    df = Counter(w for d in docs for w in set(d))
    avg = sum(map(len, docs)) / len(docs)
    return docs, df, avg


def search(query: str, k: int = 4) -> list[dict]:
    """Top-k rules for query, best first, each with a score. Ties keep document order."""
    docs, df, avg = _index()
    n, q = len(docs), tokens(query)
    scored = []
    for i, (rule, d) in enumerate(zip(load_rules(), docs)):
        tf, score = Counter(d), 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * len(d) / avg))
        if score > 0:
            scored.append((-score, i, rule))
    return [{**rule, "score": round(-s, 3)} for s, _, rule in sorted(scored)[:k]]
