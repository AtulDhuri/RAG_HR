from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import get_settings
from app.enrichment import SYNONYM_MAP


@dataclass
class QueryAnalysis:
    intent: str
    domain: str
    entities: list[str]
    concepts: list[str]
    synonyms: list[str]
    abbreviations: list[str]
    spelling_corrections: list[str]
    expanded_queries: list[str]

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def analyze_query(query: str) -> QueryAnalysis:
    normalized = " ".join(query.split())
    return _analyze_query_cached(normalized)


@lru_cache(maxsize=512)
def _analyze_query_cached(normalized: str) -> QueryAnalysis:
    model_analysis = _openai_query_analysis(normalized)
    if model_analysis:
        return model_analysis
    return _deterministic_analysis(normalized)


def _openai_query_analysis(query: str) -> QueryAnalysis | None:
    """Use OpenAI only to understand the question, never to supply document facts."""
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    prompt = (
        "Analyze this user question for retrieval. Return JSON only with keys: intent, domain, "
        "entities, concepts, synonyms, abbreviations, spelling_corrections, expanded_queries. "
        "expanded_queries must contain the original question first, then natural paraphrases and "
        "search phrases. Do not answer the question and do not add facts. Keep at most 12 queries.\n\n"
        f"Question: {query}"
    )
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_chat_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You are a query-understanding component. Never answer the question."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload["choices"][0]["message"]["content"]
        data = json.loads(raw)
        expanded = [query] + [str(item).strip() for item in data.get("expanded_queries", []) if str(item).strip() != query]
        return QueryAnalysis(
            intent=str(data.get("intent") or "fact_lookup"),
            domain=str(data.get("domain") or "unknown"),
            entities=_string_list(data.get("entities")),
            concepts=_string_list(data.get("concepts")),
            synonyms=_string_list(data.get("synonyms")),
            abbreviations=_string_list(data.get("abbreviations")),
            spelling_corrections=_string_list(data.get("spelling_corrections")),
            expanded_queries=list(dict.fromkeys(expanded))[:12],
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:20]


def _deterministic_analysis(query: str) -> QueryAnalysis:
    lower = query.lower()
    if re.search(r"\b(who|owner|responsible|approve)\b", lower): intent = "responsibility"
    elif re.search(r"\b(how|steps|process|procedure|workflow)\b", lower): intent = "procedure"
    elif re.search(r"\b(when|deadline|date|period|timing)\b", lower): intent = "timing"
    elif re.search(r"\b(why|reason|purpose)\b", lower): intent = "explanation"
    elif re.search(r"\b(compare|difference|versus|vs)\b", lower): intent = "comparison"
    else: intent = "fact_lookup"
    abbreviations = re.findall(r"\b[A-Z]{2,8}\b", query)
    entities = list(dict.fromkeys(re.findall(r"\b(?:[A-Z][\w-]*\s+){0,2}[A-Z][\w-]*\b", query)))
    concepts = [token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9'-]+", lower) if len(token) > 2]
    synonyms = sorted({syn for concept in concepts for syn in SYNONYM_MAP.get(concept, [])})
    meaningful = [token for token in concepts if token not in {"what", "where", "when", "which", "how", "does", "are", "is", "the"}]
    phrases = [" ".join(meaningful[index:index + size]) for size in (2, 3) for index in range(max(0, len(meaningful) - size + 1))]
    expanded = [query] + list(dict.fromkeys(synonyms + phrases + [" ".join(meaningful)] + concepts + abbreviations))
    return QueryAnalysis(intent, _infer_domain(lower), entities, concepts, synonyms, abbreviations, [], expanded[:30])


def _infer_domain(query: str) -> str:
    domain_terms = {
        "hr": {"employee", "onboarding", "leave", "payroll", "hiring"},
        "legal": {"contract", "clause", "termination", "liability", "agreement"},
        "finance": {"revenue", "payment", "invoice", "expense", "reimbursement"},
        "technical": {"api", "authentication", "deployment", "configuration", "endpoint"},
        "compliance": {"audit", "control", "regulation", "policy", "risk"},
        "operations": {"inventory", "workflow", "approval", "procedure", "process"},
    }
    scores = {domain: sum(term in query for term in terms) for domain, terms in domain_terms.items()}
    return max(scores, key=scores.get) if max(scores.values(), default=0) else "unknown"
