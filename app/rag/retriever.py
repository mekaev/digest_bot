import re
from dataclasses import dataclass


@dataclass(slots=True)
class RetrievedChunk:
    text: str
    score: float


class SimpleRetriever:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or []

    def add_chunks(self, chunks: list[str]) -> None:
        self.chunks.extend(chunks)

    def search(self, query: str, limit: int = 3) -> list[RetrievedChunk]:
        query_terms = set(_tokenize(query))
        if not query_terms:
            return []

        results: list[RetrievedChunk] = []
        for chunk in self.chunks:
            chunk_terms = set(_tokenize(chunk))
            if not chunk_terms:
                continue

            overlap = len(query_terms & chunk_terms)
            if overlap > 0:
                score = overlap / len(query_terms)
                results.append(RetrievedChunk(text=chunk, score=score))

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]


def _tokenize(text: str) -> list[str]:
    return re.findall(r'[a-zA-Z0-9_]+', text.lower())
