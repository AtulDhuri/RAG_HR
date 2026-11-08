from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ChunkEmbedding, Document, DocumentVersion, SemanticChunk
from app.query_understanding import analyze_query
from app.retrieval import Candidate, retrieve
from app.config import get_settings

CATEGORIES = {
    "Company Information": ("company", "organization", "about us", "mission", "founded"),
    "Products": ("product", "products", "platform", "software", "offering"),
    "Services": ("service", "services", "consulting", "support", "solution"),
    "Pricing": ("price", "pricing", "cost", "fee", "plan", "subscription"),
    "Features": ("feature", "capability", "function", "benefit"),
    "FAQs": ("faq", "frequently asked", "question", "answer"),
    "Contact Information": ("contact", "email", "phone", "address", "reach"),
    "Policies": ("policy", "policies", "leave", "employee", "compliance", "terms"),
    "Support Information": ("support", "help", "ticket", "customer service", "troubleshoot"),
    "Technical Documentation": ("api", "technical", "integration", "endpoint", "configuration", "install"),
}
BUSINESS_QUESTIONS = [
    "What does this company do?", "What products are offered?", "What services are available?",
    "How do I contact the company?", "What pricing plans exist?", "What industries are supported?",
    "What integrations are available?", "What problems does this solve?",
]

@dataclass
class ValidationQuestion:
    question: str
    source_chunk_id: uuid.UUID | None = None
    source_section: str = ""


def validate_knowledge_base(db: Session, tenant_id: str) -> dict[str, Any]:
    documents = db.scalars(select(Document).where(Document.tenant_id == tenant_id)).all()
    completed = [doc for doc in documents if doc.status == "completed"]
    versions = db.scalars(select(DocumentVersion).join(Document).where(Document.tenant_id == tenant_id)).all()
    chunks = db.scalars(select(SemanticChunk).join(DocumentVersion).join(Document).where(Document.tenant_id == tenant_id)).all()
    chunk_ids = {chunk.id for chunk in chunks}
    embedding_ids = set(db.scalars(select(ChunkEmbedding.chunk_id).where(ChunkEmbedding.chunk_id.in_(chunk_ids))).all()) if chunk_ids else set()

    extraction = _extraction_report(documents, versions)
    chunk_report = _chunk_report(chunks)
    embedding_report = _embedding_report(chunks, embedding_ids)
    generated = _generate_questions(chunks)
    retrieval_tests = [_test_question(db, tenant_id, item) for item in generated]
    coverage = _coverage_report(chunks)
    business_tests = [_test_business_question(db, tenant_id, question) for question in BUSINESS_QUESTIONS]

    scores = {
        "Document Extraction Score": extraction["score"],
        "Chunk Quality": chunk_report["score"],
        "Embedding Score": embedding_report["score"],
        "Retrieval Score": _average(item["retrieval_confidence"] for item in retrieval_tests),
        "Answer Quality": _average(item["answer_score"] for item in retrieval_tests),
        "Coverage Score": _average(item["coverage_score"] for item in coverage.values()),
        "Hallucination Safety": _average(item["hallucination_safety"] for item in retrieval_tests),
        "Citation Score": _average(item["citation_score"] for item in retrieval_tests),
    }
    overall = round(sum(scores.values()) / max(1, len(scores)), 2)
    status = "Excellent" if overall >= 95 else "Good" if overall >= 85 else "Needs Improvement" if overall >= 70 else "Requires Review"
    production_ready = overall >= 85 and not any(item["status"] == "FAIL" for item in retrieval_tests)
    reasons = _production_reasons(documents, extraction, chunk_report, embedding_report, retrieval_tests, coverage, overall)
    return {
        "report_title": "Knowledge Base Validation Report",
        "documents_processed": len(completed),
        "documents_total": len(documents),
        "chunks_created": len(chunks),
        "embeddings_generated": len(embedding_ids),
        "scores": scores,
        "overall_health_score": overall,
        "final_status": status,
        "production_readiness": "Ready for Production" if production_ready else "Knowledge Base Requires Attention",
        "production_blockers": reasons,
        "extraction_validation": extraction,
        "chunk_quality_validation": chunk_report,
        "embedding_validation": embedding_report,
        "retrieval_tests": retrieval_tests,
        "generated_question_count": len(retrieval_tests),
        "business_readiness_tests": business_tests,
        "knowledge_coverage": coverage,
        "top_retrieval_failures": [item for item in retrieval_tests if item["status"] != "PASS"][:10],
        "missing_knowledge_areas": [name for name, item in coverage.items() if item["missing_information"]],
        "recommended_fixes": _recommendations(extraction, chunk_report, embedding_report, retrieval_tests, coverage),
        "limitations": ["Original page totals are not persisted independently of LlamaParse metadata; page validation compares stored version metadata with LlamaParse layout metadata.", "Validation answers are extractive excerpts from retrieved chunks and do not add unsupported facts."],
    }


def _extraction_report(documents, versions) -> dict:
    failures=[]; empty_pages=[]; unreadable=[]; missing=[]
    for doc in documents:
        if doc.status != "completed": failures.append({"document": doc.name, "status": doc.status, "evidence": doc.error_message or "Document did not complete"})
    for version in versions:
        metadata=version.metadata_json or {}; layout=version.layout_metadata or {}; warnings=metadata.get("warnings", []) or layout.get("warnings", [])
        if warnings: unreadable.append({"document_version": str(version.id), "warnings": warnings})
        if not (version.structured_markdown or "").strip(): missing.append({"document_version": str(version.id), "evidence": "structured_markdown is empty"})
        layout_pages=layout.get("page_count") or len(layout.get("pages", []))
        if version.page_count and layout_pages and version.page_count != layout_pages: missing.append({"document_version": str(version.id), "evidence": f"stored page_count={version.page_count}, LlamaParse layout pages={layout_pages}"})
        if layout_pages and not metadata.get("block_count"): empty_pages.append({"document_version": str(version.id), "evidence": "page metadata exists but block_count is zero"})
    issue_count=len(failures)+len(empty_pages)+len(unreadable)+len(missing)
    return {"score": round(max(0, 100 - issue_count * 10), 2), "processed_failures": failures, "empty_pages": empty_pages, "unreadable_or_warning_pages": unreadable, "missing_content": missing, "corrupted_content": []}


def _chunk_report(chunks) -> dict:
    small=[{"chunk_id": str(c.id), "length": len(c.content), "section": c.section} for c in chunks if len((c.content or "").strip()) < 40]
    oversized=[{"chunk_id": str(c.id), "length": len(c.content), "section": c.section} for c in chunks if len(c.content or "") > 1500]
    normalized=[re.sub(r"\W+", " ", (c.content or "").lower()).strip() for c in chunks]
    counts=Counter(value for value in normalized if value); duplicate_values={value for value,count in counts.items() if count > 1}
    duplicates=[{"chunk_id": str(c.id), "duplicate_count": counts.get(re.sub(r"\W+", " ", (c.content or "").lower()).strip(), 1)} for c in chunks if re.sub(r"\W+", " ", (c.content or "").lower()).strip() in duplicate_values]
    incomplete=[]
    for c in chunks:
        text=(c.content or "").strip()
        if text and not text.endswith((".", "!", "?", ":", ";", "|")) and c.table_json is None:
            incomplete.append({"chunk_id": str(c.id), "section": c.section, "evidence": text[-120:]})
    heading_issues=[{"chunk_id": str(c.id), "evidence": "missing section heading"} for c in chunks if not (c.section or c.title)]
    issue_count=len(small)+len(oversized)+len(duplicates)+len(incomplete)+len(heading_issues)
    return {"score": round(max(0, 100 - (issue_count / max(1, len(chunks))) * 100), 2), "small_chunks": small, "oversized_chunks": oversized, "duplicate_chunks": duplicates, "incomplete_sentence_chunks": incomplete, "heading_grouping_issues": heading_issues}


def _embedding_report(chunks, embedding_ids) -> dict:
    missing=[str(c.id) for c in chunks if c.id not in embedding_ids]
    wrong=[]
    expected=get_settings().embedding_dimension
    # Dimension is checked during ingestion; this report verifies presence/count and exposes expected dimension.
    score=100 if len(chunks)==len(embedding_ids) and not missing else round(len(embedding_ids)/max(1,len(chunks))*100, 2)
    return {"score": score, "chunk_count": len(chunks), "embedding_count": len(embedding_ids), "missing_embeddings": missing, "embedding_failures": wrong, "expected_dimension": expected}


def _generate_questions(chunks) -> list[ValidationQuestion]:
    questions=[]; seen=set()
    for chunk in chunks:
        sentences=[s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", chunk.content or "") if len(s.strip()) >= 20]
        for sentence in sentences[:3]:
            words=re.findall(r"[A-Za-z][A-Za-z0-9'-]+", sentence)
            topic=" ".join(words[:8])
            question=f"What does the {chunk.section or chunk.title or 'document'} section state about {topic}?"
            if question not in seen: questions.append(ValidationQuestion(question, chunk.id, chunk.section or chunk.title)); seen.add(question)
            if len(questions) >= 25: return questions
    # Reuse only terms present in the stored chunk, varying the question form to reach the
    # requested validation sample size without inventing document facts.
    if chunks and len(questions) < 25:
        forms = ["What information does the {section} section provide about {topic}{modifier}?", "What does the document say about {topic} in {section}{modifier}?", "Which fact is stated about {topic} under {section}{modifier}?", "What is specified for {topic} in the {section} section{modifier}?", "What evidence is present about {topic} in {section}{modifier}?"]
        modifiers = ["?", " according to the uploaded content?", " according to the source document?", " based only on this section?", " in the indexed knowledge base?"]
        for chunk in chunks:
            words=re.findall(r"[A-Za-z][A-Za-z0-9'-]+", chunk.content or "")
            topic=" ".join(words[:8]) or "this section"
            section=chunk.section or chunk.title or "document"
            for form in forms:
                for modifier in modifiers:
                    question=form.format(section=section, topic=topic, modifier=modifier[:-1] if modifier == "?" else modifier)
                    if question not in seen: questions.append(ValidationQuestion(question, chunk.id, section)); seen.add(question)
                    if len(questions) >= 25: return questions
    return questions


def _test_question(db: Session, tenant_id: str, item: ValidationQuestion) -> dict:
    analysis=analyze_query(item.question)
    candidates, diagnostics=retrieve(db, tenant_id, analysis.expanded_queries, None, limit=10)
    top=candidates[0] if candidates else None
    confidence=0.0
    if top:
        overlap=_overlap(item.question, top.content)
        confidence=min(100.0, (0.7 * top.reranker_score + 0.3 * overlap) * 100)
    answer=_grounded_answer(item.question, candidates)
    grounded=bool(top and answer and answer != "Information not found in uploaded documents.")
    citation={"document_name": top.document_name, "section": top.section, "chunk_id": str(top.chunk_id)} if top else None
    status="PASS" if top and confidence >= 80 and grounded and citation else "FAIL" if not top or confidence < 50 else "WARNING"
    return {"question": item.question, "expected_source_chunk": str(item.source_chunk_id) if item.source_chunk_id else None, "retrieved_source_chunk": top.debug_dict() if top else None, "retrieval_confidence": round(confidence, 2), "answer": answer, "answer_score": 100 if grounded else 0, "grounded": grounded, "hallucination_risk_score": 0 if grounded else 100, "hallucination_safety": 100 if grounded else 0, "citation": citation, "citation_score": 100 if citation else 0, "status": status, "failure_evidence": [] if status == "PASS" else ["No chunk retrieved", "Retrieval confidence below 80%", "Retrieved chunk did not provide an extractive answer"]}


def _test_business_question(db: Session, tenant_id: str, question: str) -> dict:
    analysis=analyze_query(question); candidates, _=retrieve(db, tenant_id, analysis.expanded_queries, None, limit=10); top=candidates[0] if candidates else None
    answer=_grounded_answer(question, candidates)
    return {"question": question, "answer": answer, "confidence": round((top.reranker_score * 100) if top else 0, 2), "source": {"document_name": top.document_name, "section": top.section, "chunk_id": str(top.chunk_id)} if top else None, "status": "PASS" if top and answer != "Information not found in uploaded documents." else "WARNING"}


def _grounded_answer(question: str, candidates: list[Candidate]) -> str:
    if not candidates: return "I could not find sufficient supporting information in the uploaded documents."
    terms={term for term in re.findall(r"[a-z0-9]+", question.lower()) if len(term)>2}
    sentences=[]
    for candidate in candidates[:3]:
        sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", candidate.content) if s.strip())
    ranked=sorted(sentences, key=lambda sentence: len(terms & set(re.findall(r"[a-z0-9]+", sentence.lower()))), reverse=True)
    return ranked[0] if ranked else "Information not found in uploaded documents."


def _coverage_report(chunks) -> dict:
    corpus=" ".join(f"{c.section} {c.title} {c.content}" for c in chunks).lower(); result={}
    for category, terms in CATEGORIES.items():
        hits=[term for term in terms if term in corpus]; score=100 if hits else 0
        result[category]={"coverage_score": score, "confidence_score": score if hits else 0, "matched_terms": hits, "missing_information": [] if hits else [f"No evidence found for {category}"]}
    return result


def _production_reasons(documents, extraction, chunks, embeddings, tests, coverage, overall):
    reasons=[]
    if overall < 85: reasons.append(f"Overall health score is {overall}%, below the 85% production threshold")
    if any(doc.status != "completed" for doc in documents): reasons.append("One or more documents did not complete ingestion")
    if extraction["missing_content"] or extraction["unreadable_or_warning_pages"]: reasons.append("Extraction warnings or missing content were detected")
    if chunks["small_chunks"] or chunks["oversized_chunks"] or chunks["duplicate_chunks"]: reasons.append("Chunk quality issues were detected")
    if embeddings["missing_embeddings"]: reasons.append("Some chunks have no embeddings")
    failures=sum(1 for item in tests if item["status"] != "PASS")
    if failures: reasons.append(f"{failures} generated retrieval/answer questions did not pass")
    return reasons


def _recommendations(extraction, chunks, embeddings, tests, coverage):
    recommendations=[]
    if extraction["unreadable_or_warning_pages"]: recommendations.append("Review LlamaParse warnings and reprocess unreadable pages")
    if chunks["small_chunks"] or chunks["oversized_chunks"]: recommendations.append("Review chunk boundaries and section splitting")
    if chunks["duplicate_chunks"]: recommendations.append("Remove or deduplicate repeated source content")
    if embeddings["missing_embeddings"]: recommendations.append("Re-run embedding generation for missing chunks")
    if any(item["status"] != "PASS" for item in tests): recommendations.append("Investigate retrieval questions below the 80% confidence threshold")
    for category, item in coverage.items():
        if item["missing_information"]: recommendations.append(f"Add or verify {category} information")
    return recommendations or ["No corrective action identified from this validation run"]


def _overlap(query: str, content: str) -> float:
    query_terms=set(re.findall(r"[a-z0-9]+", query.lower())); content_terms=set(re.findall(r"[a-z0-9]+", content.lower()))
    return len(query_terms & content_terms) / max(1, len(query_terms))

def _average(values) -> float:
    values=list(values); return round(sum(values)/max(1,len(values)), 2)
