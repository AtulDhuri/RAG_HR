import re
from typing import Any

from app.classification import Classification


def extract_domain_fields(text: str, classification: Classification) -> dict[str, Any]:
    """Extract deterministic, provenance-free fields for indexing and later enrichment.

    The fields are deliberately conservative. They are hints for retrieval, not asserted facts.
    """
    fields: dict[str, Any] = {"document_type": classification.document_type, "domain": classification.domain}
    if classification.domain == "legal":
        fields["parties"] = _matches(text, r"(?:between|party|parties)[:\s]+([^.;\n]+)")
        fields["obligations"] = _sentences_with(text, ("shall", "must", "agrees to"))
    elif classification.domain == "finance":
        fields["financial_terms"] = _matches(text, r"\b(?:revenue|profit|loss|ebitda|assets|liabilities)\b")
    elif classification.domain == "compliance":
        fields["requirements"] = _sentences_with(text, ("must", "shall", "required", "prohibited"))
    elif classification.domain == "technical":
        fields["technical_terms"] = _matches(text, r"\b(?:api|endpoint|configuration|deployment|database|service)\b")
    else:
        fields["key_terms"] = _matches(text, r"\b[A-Za-z][A-Za-z0-9_-]{4,}\b")[:25]
    return fields


def _matches(text: str, pattern: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).strip() for match in re.finditer(pattern, text, re.I)))


def _sentences_with(text: str, terms: tuple[str, ...]) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if any(term in sentence.lower() for term in terms)
    ][:50]
