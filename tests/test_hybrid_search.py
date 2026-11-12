import math

import pytest

from evals.hybrid_search import HybridIndex, build_chunks


def _chunk(name, text):
    return {
        "path": f"src/{name}.py",
        "symbol": name,
        "line": 1,
        "end_line": 1,
        "text": text,
    }


def test_bm25_splits_snake_and_camel_case_identifiers():
    chunks = [
        _chunk("first", "class PaymentServiceHandler: pass"),
        _chunk("second", "class ProfileReader: pass"),
    ]
    result = HybridIndex(chunks, [[0.0, 1.0], [0.0, 1.0]]).search(
        "payment_service", [0.0, 0.0]
    )
    assert result[0]["symbol"] == "first"
    assert result[0]["bm25_rank"] == 1
    assert result[0]["bm25_score"] > result[1]["bm25_score"]


def test_dense_retrieval_can_recall_a_chunk_without_keyword_overlap():
    chunks = [
        _chunk("unrelated", "def inspect_planet(): pass"),
        _chunk("semantic", "def authorize_account(): pass"),
    ]
    result = HybridIndex(chunks, [[0.0, 1.0], [1.0, 0.0]]).search(
        "billing permissions", [1.0, 0.0]
    )
    assert result[0]["symbol"] == "semantic"
    assert result[0]["dense_rank"] == 1
    assert result[0]["bm25_rank"] is None
    assert result[0]["bm25_score"] == 0.0


def test_rrf_uses_source_ranks_and_returns_each_chunk_once():
    chunks = [
        _chunk("keyword", "needle needle needle filler filler"),
        _chunk("balanced", "needle filler filler filler filler"),
        _chunk("filler_a", "unrelated alpha"),
        _chunk("filler_b", "unrelated beta"),
        _chunk("filler_c", "unrelated gamma"),
        _chunk("semantic", "different vocabulary"),
    ]
    embeddings = [
        [-1.0, 0.0],
        [0.9, math.sqrt(0.19)],
        [0.5, math.sqrt(0.75)],
        [0.3, math.sqrt(0.91)],
        [0.0, 1.0],
        [1.0, 0.0],
    ]
    result = HybridIndex(chunks, embeddings).search("needle", [1.0, 0.0], limit=6)
    assert result[0]["symbol"] == "balanced"
    assert len({item["symbol"] for item in result}) == 6
    for item in result:
        lexical = 1 / (60 + item["bm25_rank"]) if item["bm25_rank"] else 0.0
        expected = lexical + 1 / (60 + item["dense_rank"])
        assert item["rrf_score"] == pytest.approx(expected)


def test_python_chunks_keep_real_source_lines_and_cover_module_code():
    source = (
        "# heading\n"
        "VALUE = 1\n"
        "\n"
        "class Worker:\n"
        "    enabled = True\n"
        "\n"
        "    def run(self):\n"
        "        return VALUE\n"
        "\n"
        "def helper():\n"
        "    return Worker()\n"
    )
    chunks = build_chunks({"src/example.py": source})
    method = next(chunk for chunk in chunks if chunk["symbol"] == "Worker.run")
    assert (method["line"], method["end_line"]) == (7, 8)
    assert method["text"] == "    def run(self):\n        return VALUE\n"
    assert any(chunk["symbol"] == "<module>" and "VALUE = 1" in chunk["text"] for chunk in chunks)

    lines = source.splitlines(keepends=True)
    for chunk in chunks:
        expected = "".join(lines[chunk["line"] - 1 : chunk["end_line"]])
        assert chunk["text"] == expected
        assert len(chunk["text"]) <= 4_000
        assert chunk["end_line"] - chunk["line"] + 1 <= 60


def test_bad_vectors_and_embedding_count_are_rejected():
    chunk = _chunk("one", "value = 1")
    with pytest.raises(ValueError, match="count"):
        HybridIndex([chunk], [])
    with pytest.raises(ValueError, match="dimension"):
        HybridIndex([chunk, chunk], [[1.0], [1.0, 2.0]])
    with pytest.raises(ValueError, match="finite"):
        HybridIndex([chunk], [[math.nan]])

    index = HybridIndex([chunk], [[1.0, 0.0]])
    with pytest.raises(ValueError, match="dimension"):
        index.search("value", [1.0])
