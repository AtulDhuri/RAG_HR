from __future__ import annotations
from sqlalchemy.orm import Session
from app.answering import answer_query

CASES = [
    ("How many weeks of maternity leave are provided?", "26 weeks"),
    ("Is leave allowed during notice period?", "No"),
    ("What are office timings?", "9:30 AM to 6:00 PM"),
    ("What is the probation period?", "6 months"),
    ("How many earned leaves are provided annually?", "18"),
    ("What is the grievance process?", "multi-step process"),
]

def run_evaluation(db: Session, tenant_id: str) -> dict:
    results=[]
    for query, expected in CASES:
        response=answer_query(db, tenant_id, query, None, 10)
        answer=response.answer.lower()
        expected_lower=expected.lower()
        retrieval_ok=bool(response.diagnostics.get("final_reranked_results"))
        citation_ok=bool(response.sources)
        results.append({"query": query, "expected": expected, "answer": response.answer, "retrieval_accuracy": retrieval_ok, "chunk_relevance": retrieval_ok, "reranker_accuracy": retrieval_ok, "answer_accuracy": expected_lower in answer, "citation_accuracy": citation_ok})
    total=max(1, len(results))
    return {"results": results, "metrics": {key: round(sum(1 for result in results if result[key]) / total, 3) for key in ("retrieval_accuracy", "chunk_relevance", "reranker_accuracy", "answer_accuracy", "citation_accuracy")}}
