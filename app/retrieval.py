from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import KnowledgeGraphFact
from app.providers import embedding_provider

_STOP = {"the", "and", "for", "how", "what", "are", "is", "of", "to", "in", "a", "an", "do", "does", "allowed"}


@dataclass
class Candidate:
    chunk_id: uuid.UUID
    document_name: str
    section: str
    subsection: str
    topic: str
    summary: str
    content: str
    pages: list[int]
    related_topics: list[str]
    dense_score: float = 0.0
    lexical_score: float = 0.0
    bm25_score: float = 0.0
    reranker_score: float = 0.0
    retrieval_sources: list[str] = field(default_factory=list)
    graph_facts: list[str] = field(default_factory=list)
    document_version_id: uuid.UUID | None = None
    section_id: uuid.UUID | None = None
    ordinal: int = 0

    @property
    def score(self) -> float:
        return max(0.0, min(1.0, self.reranker_score or (0.7 * self.dense_score + 0.3 * self.lexical_score)))

    def debug_dict(self) -> dict:
        value = asdict(self)
        value["chunk_id"] = str(self.chunk_id)
        if self.document_version_id:
            value["document_version_id"] = str(self.document_version_id)
        if self.section_id:
            value["section_id"] = str(self.section_id)
        value["score"] = round(self.score, 4)
        return value


def retrieve(db: Session, tenant_id: str, expanded_queries: list[str], document_ids: list[uuid.UUID] | None, limit: int = 50) -> tuple[list[Candidate], dict]:
    """True hybrid retrieval: vector-20 + BM25-20, fused, deduplicated, reranked."""
    original = expanded_queries[0] if expanded_queries else ""
    provider = embedding_provider()
    query_vectors = provider.embed(expanded_queries[:5] or [original])
    query_vector = _expanded_query_vector(query_vectors)
    vector_literal = "[" + ",".join(str(value) for value in query_vector) + "]"
    document_filter = "AND d.id = ANY(:document_ids)" if document_ids else ""
    params: dict = {"tenant_id": tenant_id, "query_vector": vector_literal, "vector_limit": 20}
    if document_ids:
        params["document_ids"] = [str(item) for item in document_ids]
    vector_rows = db.execute(text(f"""
        SELECT c.id AS chunk_id, c.document_version_id, c.section_id, c.ordinal, d.name AS document_name, c.section, c.subsection, c.topic,
               c.summary, c.content, c.source_pages AS pages, c.related_topics,
               GREATEST(0, 1 - (e.embedding <=> CAST(:query_vector AS vector))) AS dense_score
        FROM semantic_chunks c JOIN document_versions v ON v.id = c.document_version_id
        JOIN documents d ON d.id = v.document_id JOIN chunk_embeddings e ON e.chunk_id = c.id
        WHERE d.tenant_id = :tenant_id AND d.status = 'completed' {document_filter}
        ORDER BY e.embedding <=> CAST(:query_vector AS vector) LIMIT :vector_limit
    """), params).mappings().all()
    vector_candidates = [_candidate(row, dense=float(row["dense_score"] or 0), source="vector") for row in vector_rows]

    # Use PostgreSQL full-text ranking over a GIN index instead of downloading and
    # retokenizing the entire tenant corpus on every request. This is the fast BM25-like
    # keyword leg; the application BM25 implementation remains available for offline use.
    keyword_query = " OR ".join(expanded_queries[:5])
    keyword_rows = db.execute(text(f"""
        SELECT c.id AS chunk_id, c.document_version_id, c.section_id, c.ordinal,
               d.name AS document_name, c.section, c.subsection, c.topic, c.summary,
               c.content, c.source_pages AS pages, c.related_topics,
               ts_rank_cd(to_tsvector('simple', c.search_text), websearch_to_tsquery('simple', :keyword_query)) AS keyword_score
        FROM semantic_chunks c
        JOIN document_versions v ON v.id = c.document_version_id
        JOIN documents d ON d.id = v.document_id
        WHERE d.tenant_id = :tenant_id AND d.status = 'completed' {document_filter}
          AND to_tsvector('simple', c.search_text) @@ websearch_to_tsquery('simple', :keyword_query)
        ORDER BY keyword_score DESC
        LIMIT 20
    """), {"tenant_id": tenant_id, "keyword_query": keyword_query, **({"document_ids": [str(item) for item in document_ids]} if document_ids else {})}).mappings().all()
    if not keyword_rows:
        # Compatibility fallback for projects before migration 0004 and phrases that
        # full-text normalization cannot match.
        terms = list(dict.fromkeys(token for term in expanded_queries[:5] for token in _tokens(term)))[:12]
        conditions = " OR ".join(f"c.search_text ILIKE :keyword_{index}" for index in range(len(terms))) or "FALSE"
        fallback_params = {"tenant_id": tenant_id, **{f"keyword_{index}": f"%{term}%" for index, term in enumerate(terms)}}
        if document_ids:
            fallback_params["document_ids"] = [str(item) for item in document_ids]
        keyword_rows = db.execute(text(f"""
            SELECT c.id AS chunk_id, c.document_version_id, c.section_id, c.ordinal,
                   d.name AS document_name, c.section, c.subsection, c.topic, c.summary,
                   c.content, c.source_pages AS pages, c.related_topics, 0.25 AS keyword_score
            FROM semantic_chunks c
            JOIN document_versions v ON v.id = c.document_version_id
            JOIN documents d ON d.id = v.document_id
            WHERE d.tenant_id = :tenant_id AND d.status = 'completed' {document_filter}
              AND ({conditions})
            LIMIT 20
        """), fallback_params).mappings().all()
    keyword_candidates = []
    for row in keyword_rows:
        score = float(row["keyword_score"] or 0)
        candidate = _candidate(row, lexical=min(1.0, score), source="keyword")
        candidate.bm25_score = score
        keyword_candidates.append(candidate)
    merged = _merge(vector_candidates, keyword_candidates)
    _attach_parent_children(db, merged)
    _attach_graph_evidence(db, tenant_id, expanded_queries, merged)
    _rerank_candidates(original, merged)
    final = sorted(merged, key=lambda item: item.reranker_score, reverse=True)[:limit]
    debug = {
        "strategy": "hybrid_vector_bm25_query_expansion_parent_child_cross_encoder",
        "reranker_backend": _LAST_RERANK_BACKEND,
        "query_embedding_generated": bool(query_vector),
        "query_embedding_preview": [round(value, 6) for value in query_vector[:16]],
        "embedding_dimension": len(query_vector),
        "vector_results": [item.debug_dict() for item in vector_candidates[:20]],
        "keyword_results": [item.debug_dict() for item in keyword_candidates[:20]],
        "merged_count": len(merged),
        "final_reranked_results": [item.debug_dict() for item in final],
    }
    return final, debug


def _candidate(row, dense=0.0, lexical=0.0, source="") -> Candidate:
    return Candidate(row["chunk_id"], row["document_name"], row["section"] or "Document", row["subsection"] or "", row["topic"] or "", row["summary"] or "", row["content"], row["pages"] or [], row["related_topics"] or [], dense_score=dense, lexical_score=lexical, retrieval_sources=[source] if source else [], document_version_id=row.get("document_version_id"), section_id=row.get("section_id"), ordinal=int(row.get("ordinal") or 0))


def _merge(*groups: list[Candidate]) -> list[Candidate]:
    by_id: dict[uuid.UUID, Candidate] = {}
    for group in groups:
        for item in group:
            existing = by_id.get(item.chunk_id)
            if not existing:
                by_id[item.chunk_id] = item
            else:
                existing.dense_score = max(existing.dense_score, item.dense_score)
                existing.lexical_score = max(existing.lexical_score, item.lexical_score)
                existing.retrieval_sources = list(dict.fromkeys(existing.retrieval_sources + item.retrieval_sources))
    return list(by_id.values())


def _tokens(text_value: str) -> list[str]:
    return [x for x in re.findall(r"[a-z0-9]+", text_value.lower()) if len(x) > 2 and x not in _STOP]


def _bm25(query: str, document: str, corpus: list[str]) -> float:
    q = set(_tokens(query)); terms = _tokens(document)
    if not q or not terms: return 0.0
    avg = sum(len(_tokens(item)) for item in corpus) / max(1, len(corpus))
    length = len(terms); score = 0.0
    for term in q:
        tf = terms.count(term)
        df = sum(term in set(_tokens(item)) for item in corpus)
        if tf and df:
            idf = math.log(1 + (len(corpus) - df + 0.5) / (df + 0.5))
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.75 + 0.25 * length / max(1, avg)))
    return score


def _rerank(query: str, candidate: Candidate) -> float:
    q = set(_tokens(query)); text_value = " ".join([candidate.section, candidate.subsection, candidate.topic, candidate.content]).lower()
    overlap = len(q & set(_tokens(text_value))) / max(1, len(q))
    phrase = 0.18 if query.lower() in candidate.content.lower() else 0.0
    heading = 0.12 if q & set(_tokens(candidate.section + " " + candidate.topic)) else 0.0
    # Cross-encoder-compatible deterministic fallback when sentence-transformers is not installed.
    return max(0.0, min(1.0, 0.42 * candidate.dense_score + 0.28 * candidate.lexical_score + 0.12 * overlap + phrase + heading + (0.08 if candidate.graph_facts else 0)))


def _attach_graph_evidence(db: Session, tenant_id: str, queries: list[str], candidates: list[Candidate]) -> None:
    if not candidates: return
    terms = _tokens(" ".join(queries))
    by_chunk = {candidate.chunk_id: candidate for candidate in candidates}
    facts = db.query(KnowledgeGraphFact).filter(KnowledgeGraphFact.tenant_id == tenant_id).all()
    for fact in facts:
        if any(term in f"{fact.subject} {fact.predicate} {fact.object}".lower() for term in terms):
            candidate = by_chunk.get(fact.evidence_chunk_id)
            if candidate: candidate.graph_facts.append(f"{fact.subject} {fact.predicate} {fact.object}")


_CROSS_ENCODER = None
_CROSS_ENCODER_UNAVAILABLE = False
_LAST_RERANK_BACKEND = "heuristic_fallback"


def _attach_parent_children(db: Session, candidates: list[Candidate]) -> None:
    """Expand a matching child chunk with sibling chunks from the same section parent."""
    if not candidates:
        return
    existing = {candidate.chunk_id for candidate in candidates}
    anchors = [candidate for candidate in candidates[:3] if candidate.document_version_id and candidate.section_id]
    for anchor in anchors:
        rows = db.execute(text("""
            SELECT c.id AS chunk_id, c.document_version_id, c.section_id, c.ordinal,
                   d.name AS document_name, c.section, c.subsection, c.topic, c.summary,
                   c.content, c.source_pages AS pages, c.related_topics
            FROM semantic_chunks c
            JOIN document_versions v ON v.id = c.document_version_id
            JOIN documents d ON d.id = v.document_id
            WHERE c.document_version_id = :version_id AND c.section_id = :section_id
            ORDER BY c.ordinal
            LIMIT 4
        """), {"version_id": anchor.document_version_id, "section_id": anchor.section_id}).mappings().all()
        for row in rows:
            if row["chunk_id"] in existing:
                continue
            child = _candidate(row, dense=anchor.dense_score * 0.92, lexical=anchor.lexical_score * 0.92, source="parent_child")
            candidates.append(child)
            existing.add(child.chunk_id)


def _rerank_candidates(query: str, candidates: list[Candidate]) -> None:
    global _LAST_RERANK_BACKEND
    pairs = [(query, " ".join([candidate.section, candidate.subsection, candidate.topic, candidate.content])) for candidate in candidates[:15]]
    rerank_candidates = candidates[:15]
    model_scores = _cross_encoder_scores(pairs)
    _LAST_RERANK_BACKEND = "cross_encoder" if model_scores is not None else "heuristic_fallback"
    for index, candidate in enumerate(rerank_candidates):
        heuristic = _rerank(query, candidate)
        if model_scores is None:
            candidate.reranker_score = heuristic
        else:
            candidate.reranker_score = max(0.0, min(1.0, 0.75 * model_scores[index] + 0.25 * heuristic))


def _cross_encoder_scores(pairs: list[tuple[str, str]]) -> list[float] | None:
    """Use a real cross-encoder when installed; retain a deterministic fallback otherwise."""
    global _CROSS_ENCODER, _CROSS_ENCODER_UNAVAILABLE
    if _CROSS_ENCODER_UNAVAILABLE:
        return None
    try:
        from sentence_transformers import CrossEncoder
        from app.config import get_settings
        if _CROSS_ENCODER is None:
            _CROSS_ENCODER = CrossEncoder(get_settings().reranker_model)
        raw_scores = _CROSS_ENCODER.predict(pairs)
        values = [float(score) for score in raw_scores]
        if not values:
            return None
        minimum, maximum = min(values), max(values)
        if minimum == maximum:
            return [0.5 for _ in values]
        return [(value - minimum) / (maximum - minimum) for value in values]
    except (ImportError, OSError, RuntimeError, ValueError):
        _CROSS_ENCODER_UNAVAILABLE = True
        return None


def _expanded_query_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    if len(vectors) == 1:
        return vectors[0]
    expansion_mean = [sum(vector[index] for vector in vectors[1:]) / (len(vectors) - 1) for index in range(len(vectors[0]))]
    return [0.55 * vectors[0][index] + 0.45 * expansion_mean[index] for index in range(len(vectors[0]))]


def _bm25_scores(query: str, documents: list[str]) -> list[float]:
    """Compute BM25 once over cached token lists instead of retokenizing every row and term."""
    tokenized = [_tokens(document) for document in documents]
    document_frequency: dict[str, int] = {}
    for tokens in tokenized:
        for term in set(tokens):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    query_terms = set(_tokens(query))
    average_length = sum(len(tokens) for tokens in tokenized) / max(1, len(tokenized))
    scores = []
    for terms in tokenized:
        score = 0.0
        length = len(terms)
        for term in query_terms:
            frequency = terms.count(term)
            document_count = document_frequency.get(term, 0)
            if not frequency or not document_count:
                continue
            inverse_frequency = math.log(1 + (len(tokenized) - document_count + 0.5) / (document_count + 0.5))
            score += inverse_frequency * (frequency * 2.2) / (frequency + 1.2 * (0.75 + 0.25 * length / max(1, average_length)))
        scores.append(score)
    return scores
