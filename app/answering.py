from __future__ import annotations
import re
import uuid
from sqlalchemy.orm import Session
from app.models import Citation, QueryLog
from app.providers import answer_provider
from app.query_understanding import analyze_query
from app.retrieval import Candidate, retrieve
from app.schemas import AnswerResponse, SourceCitation

INSUFFICIENT = "I could not find sufficient supporting information in the uploaded documents."
_GENERIC_FAILURES = (INSUFFICIENT.lower(), "information not found", "not enough information", "cannot answer", "unable to answer")
_QUERY_STOPWORDS = {"the", "what", "where", "when", "which", "how", "many", "does", "are", "is", "of", "to", "in", "a", "an", "for", "and", "can", "be"}

def answer_query(db: Session, tenant_id: str, query: str, document_ids: list[uuid.UUID] | None, top_k: int) -> AnswerResponse:
    analysis = analyze_query(query)
    candidates, retrieval_debug = retrieve(db, tenant_id, analysis.expanded_queries, document_ids, limit=50)
    selected = candidates[:max(5, min(8, top_k))]
    confidence, coverage, strength, decision = _confidence(query, selected, analysis.expanded_queries)
    context = _context(selected)
    # Rejection is evidence-only. A relevant evidence chunk must never be rejected by a low aggregate score.
    has_support = _has_explicit_support_queries(analysis.expanded_queries[:12], selected)
    if has_support:
        response_text = answer_provider().answer(query, context)
        # A model must not turn retrieved evidence into a generic refusal. Fall back to
        # an extractive answer when the provider rejects despite explicit support.
        if _is_generic_failure(response_text):
            response_text = _extractive_answer(query, selected)
        confidence = "High"
        decision = "Answered because explicit supporting evidence was retrieved"
    elif not selected:
        confidence = "Low"
        response_text, decision = INSUFFICIENT, "Rejected because no relevant chunks retrieved"
    else:
        confidence = "Low"
        response_text, decision = INSUFFICIENT, "Rejected because missing supporting evidence"
    if not response_text.strip(): response_text = INSUFFICIENT
    log = QueryLog(tenant_id=tenant_id, query=query, intent=analysis.intent, expanded_queries=analysis.expanded_queries, result_count=len(candidates), confidence=confidence.lower())
    db.add(log); db.flush()
    sources = []
    for rank, candidate in enumerate(selected, 1):
        evidence_strength = min(1.0, 0.55 + (0.2 if candidate.summary else 0) + (0.15 if candidate.pages else 0))
        db.add(Citation(query_log_id=log.id, chunk_id=candidate.chunk_id, rank=rank, relevance_score=candidate.score, evidence_strength=evidence_strength))
        sources.append(SourceCitation(document_name=candidate.document_name, section=" / ".join(filter(None, [candidate.section, candidate.subsection])), page_numbers=[int(p) for p in candidate.pages if str(p).isdigit()], chunk_id=candidate.chunk_id, relevance_score=round(candidate.score, 4), evidence_strength=round(evidence_strength, 4)))
    db.commit()
    diagnostics = dict(retrieval_debug)
    diagnostics.update({"query": query, "query_intent": analysis.intent, "expanded_search_terms": analysis.expanded_queries, "evidence_found": has_support, "final_confidence": confidence, "failure_reasons": [] if has_support else (["No relevant chunk retrieved"] if not candidates else ["Missing supporting evidence"]), "final_prompt_sent_to_llm": context, "confidence_calculation": {"confidence": confidence, "coverage": round(coverage, 4), "strength": round(strength, 4), "explicit_support": has_support}, "coverage_calculation": f"normalized query evidence coverage={coverage:.4f}", "chunks_sent_to_llm": [_debug_chunk(c) for c in selected], "final_decision_reason": decision})
    related_topics = list(dict.fromkeys(topic for candidate in selected for topic in candidate.related_topics))[:10]
    reasoning = f"Hybrid vector/BM25 retrieval and reranking returned {len(candidates)} candidates. Coverage={coverage:.2f}; reranker strength={strength:.2f}. {decision}."
    return AnswerResponse(answer=response_text, confidence=confidence, sources=sources, reasoning_summary=reasoning, related_topics=related_topics, query_analysis=analysis.as_dict(), diagnostics=diagnostics)

def _debug_chunk(candidate: Candidate) -> dict:
    return {"chunk_id": str(candidate.chunk_id), "document_name": candidate.document_name, "section": candidate.section, "pages": candidate.pages, "content": candidate.content, "reranker_score": round(candidate.reranker_score, 4)}

def _context(candidates: list[Candidate]) -> str:
    return "\n\n".join(f"[S{i}] Document: {c.document_name}; Section: {c.section}; Pages: {c.pages}\nContent: {c.content}" for i, c in enumerate(candidates, 1))

def _normalize(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 2 and term not in _QUERY_STOPWORDS}

def _is_generic_failure(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return not lowered or any(phrase in lowered for phrase in _GENERIC_FAILURES)

def _extractive_answer(query: str, candidates: list[Candidate]) -> str:
    terms = _normalize(query)
    sentences = []
    for candidate in candidates[:10]:
        sentences.extend(sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|\n+", candidate.content) if sentence.strip())
    ranked = sorted(sentences, key=lambda sentence: len(terms & _normalize(sentence)), reverse=True)
    return " ".join(ranked[:2]) if ranked else INSUFFICIENT

def _confidence(query: str, candidates: list[Candidate], expanded_queries: list[str] | None = None) -> tuple[str, float, float, str]:
    if not candidates: return "Low", 0.0, 0.0, "Rejected because no relevant chunks retrieved"
    terms = {t for t in _normalize(query) if len(t) > 2}
    overlaps = [len(terms & _normalize(c.content)) / max(1, len(terms)) for c in candidates]
    coverage = min(1.0, max(overlaps) * 1.5)
    strength = max(c.score for c in candidates)
    support_queries = expanded_queries[:12] if expanded_queries else [query]
    if _has_explicit_support_queries(support_queries, candidates): return "High", coverage, strength, "Answered because explicit supporting evidence was retrieved"
    return ("Medium" if strength >= .35 else "Low"), coverage, strength, "Rejected because reranker score below threshold"

def _has_explicit_support_queries(queries: list[str], candidates: list[Candidate]) -> bool:
    return any(_has_explicit_support(query, candidates) for query in queries)


def _has_explicit_support(query: str, candidates: list[Candidate]) -> bool:
    terms = {t for t in _normalize(query) if len(t) > 2}
    for c in candidates[:10]:
        content = c.content.lower()
        overlap = len(terms & _normalize(content)) / max(1, len(terms))
        if overlap >= 0.35 or query.lower() in content or (c.reranker_score >= 0.55 and c.lexical_score > 0): return True
    return False
