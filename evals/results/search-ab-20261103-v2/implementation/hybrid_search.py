"""Small, dependency-free hybrid retrieval primitives for evaluation runs."""

from __future__ import annotations

import ast
from collections import Counter
import math
import re
from numbers import Real


MAX_CHUNK_LINES = 60
MAX_CHUNK_CHARS = 4_000
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60

_CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_WORD = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]")
_DEFINITION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_METHOD = (ast.FunctionDef, ast.AsyncFunctionDef)


def _tokens(value: str) -> list[str]:
    """Tokenize prose and code identifiers, including snake/camel case."""
    expanded = value.replace("_", " ")
    expanded = _CAMEL_ACRONYM.sub(r"\1 \2", expanded)
    expanded = _CAMEL_WORD.sub(r"\1 \2", expanded)
    return [match.group(0).lower() for match in _TOKEN.finditer(expanded)]


def _node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno, *(item.lineno for item in decorators)])


def _class_spans(node: ast.ClassDef) -> list[tuple[int, int, str]]:
    """Separate a class shell from its direct methods without duplicating text."""
    spans: list[tuple[int, int, str]] = []
    cursor = _node_start(node)
    methods = sorted((child for child in node.body if isinstance(child, _METHOD)), key=_node_start)
    for method in methods:
        start = _node_start(method)
        if cursor < start:
            spans.append((cursor, start - 1, node.name))
        spans.append((start, method.end_lineno, f"{node.name}.{method.name}"))
        cursor = method.end_lineno + 1
    if cursor <= node.end_lineno:
        spans.append((cursor, node.end_lineno, node.name))
    return spans


def _python_spans(source: str) -> list[tuple[int, int, str]]:
    line_count = len(source.splitlines())
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [(1, line_count, "<module>")] if line_count else []

    spans: list[tuple[int, int, str]] = []
    cursor = 1
    definitions = sorted((node for node in tree.body if isinstance(node, _DEFINITION)), key=_node_start)
    for node in definitions:
        start = _node_start(node)
        if cursor < start:
            spans.append((cursor, start - 1, "<module>"))
        if isinstance(node, ast.ClassDef):
            spans.extend(_class_spans(node))
        else:
            spans.append((start, node.end_lineno, node.name))
        cursor = node.end_lineno + 1
    if cursor <= line_count:
        spans.append((cursor, line_count, "<module>"))
    return spans


def _chunk_span(
    path: str,
    lines: list[str],
    start: int,
    end: int,
    symbol: str,
) -> list[dict]:
    chunks: list[dict] = []
    parts: list[str] = []
    part_start = start
    part_end = start
    char_count = 0

    def emit() -> None:
        nonlocal parts, char_count
        text = "".join(parts)
        if text.strip():
            chunks.append(
                {
                    "path": path,
                    "symbol": symbol,
                    "line": part_start,
                    "end_line": part_end,
                    "text": text,
                }
            )
        parts = []
        char_count = 0

    for line_number in range(start, end + 1):
        line = lines[line_number - 1]
        for offset in range(0, len(line), MAX_CHUNK_CHARS):
            piece = line[offset : offset + MAX_CHUNK_CHARS]
            too_many_lines = parts and line_number - part_start + 1 > MAX_CHUNK_LINES
            too_many_chars = parts and char_count + len(piece) > MAX_CHUNK_CHARS
            if too_many_lines or too_many_chars:
                emit()
            if not parts:
                part_start = line_number
            parts.append(piece)
            part_end = line_number
            char_count += len(piece)
    if parts:
        emit()
    return chunks


def build_chunks(files: dict[str, str]) -> list[dict]:
    """Build source-backed chunks using only the supplied path/content pairs."""
    chunks: list[dict] = []
    for path, source in files.items():
        if not isinstance(path, str) or not isinstance(source, str):
            raise TypeError("files must map string paths to string contents")
        lines = source.splitlines(keepends=True)
        if not lines:
            continue
        spans = _python_spans(source) if path.lower().endswith(".py") else [(1, len(lines), "<file>")]
        for start, end, symbol in spans:
            chunks.extend(_chunk_span(path, lines, start, end, symbol))
    return chunks


def _validated_vector(vector: list[float], dimension: int, label: str) -> tuple[float, ...]:
    if not isinstance(vector, (list, tuple)):
        raise ValueError(f"{label} must be a list of numbers")
    if len(vector) != dimension:
        raise ValueError(f"{label} dimension must be {dimension}, got {len(vector)}")
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
        raise ValueError(f"{label} values must be real numbers")
    result = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} values must be finite")
    return result


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class HybridIndex:
    """An in-memory BM25/cosine index fused with reciprocal rank fusion."""

    def __init__(self, chunks: list[dict], embeddings: list[list[float]]):
        if len(chunks) != len(embeddings):
            raise ValueError("embedding count must match chunk count")
        dimension = len(embeddings[0]) if embeddings else 0
        if embeddings and not dimension:
            raise ValueError("embedding vectors must not be empty")
        self.chunks = [dict(chunk) for chunk in chunks]
        self.embeddings = [
            _validated_vector(vector, dimension, f"embedding {index}")
            for index, vector in enumerate(embeddings)
        ]

        self._documents = [
            _tokens(f"{chunk.get('path', '')} {chunk.get('symbol', '')} {chunk.get('text', '')}")
            for chunk in self.chunks
        ]
        self._term_frequencies = [Counter(document) for document in self._documents]
        self._average_length = (
            sum(map(len, self._documents)) / len(self._documents) if self._documents else 0.0
        )
        self._document_frequency = Counter(token for doc in self._documents for token in set(doc))
        self.stats = {
            "chunk_count": len(self.chunks),
            "embedding_dimension": dimension,
            "bm25": {"k1": BM25_K1, "b": BM25_B},
            "dense": {"metric": "cosine"},
            "fusion": {"method": "reciprocal_rank_fusion", "k": RRF_K},
            "bm25_k1": BM25_K1,
            "bm25_b": BM25_B,
            "dense_metric": "cosine",
            "rrf_k": RRF_K,
        }

    def _bm25_scores(self, query: str) -> list[float]:
        query_terms = Counter(_tokens(query))
        document_count = len(self.chunks)
        scores: list[float] = []
        for document, frequencies in zip(self._documents, self._term_frequencies):
            length_ratio = len(document) / self._average_length if self._average_length else 0.0
            score = 0.0
            for term, query_frequency in query_terms.items():
                frequency = frequencies[term]
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                inverse_frequency = math.log(
                    1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + BM25_K1 * (1.0 - BM25_B + BM25_B * length_ratio)
                score += query_frequency * inverse_frequency * frequency * (BM25_K1 + 1.0) / denominator
            scores.append(score)
        return scores

    def search(
        self,
        query: str,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit <= 0 or not self.chunks:
            return []
        dimension = self.stats["embedding_dimension"]
        vector = _validated_vector(query_embedding, dimension, "query embedding")

        bm25_scores = self._bm25_scores(query)
        dense_scores = [_cosine(vector, embedding) for embedding in self.embeddings]
        bm25_order = sorted(
            (index for index, score in enumerate(bm25_scores) if score > 0.0),
            key=lambda i: (-bm25_scores[i], i),
        )
        dense_order = sorted(range(len(self.chunks)), key=lambda i: (-dense_scores[i], i))
        bm25_ranks = {index: rank for rank, index in enumerate(bm25_order, 1)}
        dense_ranks = {index: rank for rank, index in enumerate(dense_order, 1)}

        def rrf_score(index: int) -> float:
            lexical = 1.0 / (RRF_K + bm25_ranks[index]) if index in bm25_ranks else 0.0
            return lexical + 1.0 / (RRF_K + dense_ranks[index])

        fused = sorted(range(len(self.chunks)), key=lambda i: (-rrf_score(i), i))
        results: list[dict] = []
        for index in fused[: min(limit, len(fused))]:
            result = dict(self.chunks[index])
            result.update(
                {
                    "bm25_score": bm25_scores[index],
                    "bm25_rank": bm25_ranks.get(index),
                    "dense_score": dense_scores[index],
                    "dense_rank": dense_ranks[index],
                    "rrf_score": rrf_score(index),
                }
            )
            results.append(result)
        return results
