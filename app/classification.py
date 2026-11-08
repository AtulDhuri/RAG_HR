from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Classification:
    document_type: str
    domain: str
    confidence: float
    signals: list[str]


def classify_document(text: str) -> Classification:
    sample = text.lower()
    rules = {
        "policy": ("policy", "procedure", "must", "shall", "compliance"),
        "contract": ("agreement", "party", "whereas", "terms and conditions", "liability"),
        "financial_report": ("balance sheet", "revenue", "income statement", "fiscal", "ebitda"),
        "technical_documentation": ("api", "configuration", "deployment", "endpoint", "architecture"),
    }
    scores = {kind: sum(sample.count(term) for term in terms) for kind, terms in rules.items()}
    document_type, score = max(scores.items(), key=lambda item: item[1], default=("generic", 0))
    if score == 0:
        document_type = "generic"
    domain = {
        "policy": "compliance",
        "contract": "legal",
        "financial_report": "finance",
        "technical_documentation": "technical",
        "generic": "general",
    }[document_type]
    confidence = min(1.0, 0.45 + score * 0.05) if score else 0.2
    signals = [term for term in rules.get(document_type, ()) if term in sample]
    return Classification(document_type, domain, round(confidence, 3), signals)


def classification_dict(result: Classification) -> dict:
    return asdict(result)
