from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    "about",
    "after",
    "among",
    "and",
    "are",
    "before",
    "from",
    "into",
    "that",
    "their",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "will",
    "have",
    "must",
    "shall",
    "should",
    "the",
}
SYNONYM_MAP = {
    "onboarding": [
        "orientation",
        "induction",
        "joining process",
        "new hire process",
        "employee setup",
    ],
    "refund": ["return", "reimbursement", "credit", "compensation"],
    "leave": ["pto", "vacation", "annual leave", "paid leave", "time off", "maternity leave", "parental leave", "family leave"],
    "maternity": ["maternity leave", "pregnancy leave", "parental leave", "maternal leave", "childbirth leave"],
    "maternal": ["maternity", "pregnancy", "childbirth", "parental"],
    "pregnancy": ["maternity", "maternal", "childbirth", "parental leave"],
    "childbirth": ["maternity", "maternal leave", "pregnancy leave"],
    "authentication": [
        "authorization",
        "login",
        "access control",
        "identity verification",
        "token",
    ],
    "approval": ["authorization", "sign-off", "review", "consent", "decision"],
    "workday": ["working hours", "office hours", "office timings", "business hours", "working time"],
    "timing": ["office timings", "working hours", "business hours", "schedule"],
    "begin": ["start", "commencement", "opening time"],
    "end": ["finish", "closing time", "close of business"],
    "office": ["workplace", "working hours", "office timings"],
}


def enrich_chunk(
    title: str, section: str, content: str, pages: list[int], table_json: dict | None = None
) -> dict:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", content) if part.strip()]
    summary = sentences[0][:500] if sentences else title
    words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", content)]
    keywords = [
        word for word, _ in Counter(word for word in words if word not in STOPWORDS).most_common(12)
    ]
    concepts = _concepts(title, content)
    entities = extract_entities(content)
    synonyms = sorted(
        {syn for keyword in [title.lower(), *keywords] for syn in SYNONYM_MAP.get(keyword, [])}
    )[:20]
    questions = _questions(title or section, keywords, synonyms)
    answer_variations = [
        summary,
        f"The documents describe {title.lower() or 'this topic'} as follows: {summary}",
    ]
    related_topics = keywords[:6]
    embedding_text = "\n".join(
        [
            f"Title: {title}",
            f"Section: {section}",
            f"Summary: {summary}",
            f"Keywords: {', '.join(keywords)}",
            f"Questions: {' | '.join(questions)}",
            f"Synonyms: {', '.join(synonyms)}",
            f"Content: {content}",
        ]
    )
    return {
        "summary": summary,
        "keywords": keywords,
        "entities": entities,
        "concepts": concepts,
        "question_variations": questions,
        "answer_variations": answer_variations,
        "synonyms": synonyms,
        "related_topics": related_topics,
        "source_pages": pages,
        "embedding_text": embedding_text,
        "search_text": embedding_text.lower(),
        "table_json": table_json,
    }


def extract_entities(text: str) -> list[dict[str, str]]:
    candidates = re.findall(r"\b(?:[A-Z][A-Za-z0-9&/-]*\s+){0,3}[A-Z][A-Za-z0-9&/-]*\b", text)
    entities: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = " ".join(candidate.split()).strip(" ,.:;()")[:512]
        if (
            len(value) < 3
            or value.lower() in seen
            or value.lower() in {"Table Data", "The Documents"}
        ):
            continue
        seen.add(value.lower())
        entity_type = (
            "organization"
            if re.search(r"\b(inc|ltd|llc|corp|department|team|company)\b", value, re.I)
            else "concept"
        )
        entities.append({"name": value, "type": entity_type})
    return entities[:30]


def extract_graph_facts(
    content: str, entities: list[dict[str, str]]
) -> list[dict[str, str | float]]:
    facts: list[dict[str, str | float]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", content):
        for entity in entities:
            name = entity["name"]
            if name not in sentence:
                continue
            pattern = (
                rf"{re.escape(name)}\s+"
                r"(is|uses|requires|includes|approves|reports to|depends on)\s+([^.;]+)"
            )
            match = re.search(pattern, sentence, re.I)
            if match:
                facts.append(
                    {
                        "subject": name,
                        "predicate": match.group(1).lower(),
                        "object": match.group(2).strip()[:512],
                        "confidence": 0.7,
                    }
                )
    return facts[:20]


def _concepts(title: str, content: str) -> list[str]:
    concepts = [title.strip()] if title.strip() else []
    for phrase in re.findall(
        r"\b(?:process|procedure|policy|workflow|requirements?|terms?|method|standard|matrix|report)\b",
        content,
        re.I,
    ):
        if phrase.lower() not in {item.lower() for item in concepts}:
            concepts.append(phrase.lower())
    return concepts[:10]


def _questions(title: str, keywords: list[str], synonyms: list[str]) -> list[str]:
    subject = title or (keywords[0] if keywords else "this topic")
    questions = [
        f"What is {subject}?",
        f"How does {subject} work?",
        f"What are the requirements for {subject}?",
        f"Who is responsible for {subject}?",
        f"When does {subject} apply?",
        f"What steps are involved in {subject}?",
        f"What documents or inputs are needed for {subject}?",
        f"What rules govern {subject}?",
        f"What exceptions apply to {subject}?",
        f"Where is {subject} described?",
    ]
    questions.extend(
        f"What is the relationship between {synonym} and {subject}?" for synonym in synonyms[:4]
    )
    return questions[:14]
