from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.config import get_settings


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AnswerProvider(Protocol):
    def answer(self, query: str, context: str) -> str: ...


class HashEmbeddingProvider:
    """Deterministic local fallback. Use a managed embedding model in production."""

    model_name = "hash-embedding-v1"

    def __init__(self, dimension: int):
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        tokens = re.findall(r"[\w'-]+", text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=16).digest()
            for offset in range(0, len(digest), 4):
                index = int.from_bytes(digest[offset : offset + 4], "big") % self.dimension
                values[index] += 1.0 if digest[offset] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


class OpenAIEmbeddingProvider:
    model_name = "openai"

    def __init__(self, api_key: str, model: str, dimension: int):
        self.api_key = api_key
        self.model = model
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed in bounded batches so total request tokens stay below OpenAI limits."""
        safe_texts = [text[:24_000] for text in texts]
        embeddings: list[list[float]] = []
        batch: list[str] = []
        estimated_tokens = 0
        for text in safe_texts:
            text_tokens = max(1, len(text) // 4)
            if batch and estimated_tokens + text_tokens > 200_000:
                embeddings.extend(self._embed_batch(batch))
                batch = []
                estimated_tokens = 0
            batch.append(text)
            estimated_tokens += text_tokens
        if batch:
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self.model, "input": texts}
        if self.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.dimension
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        if not response.is_success:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("error", body)) if isinstance(body, dict) else str(body)
            except ValueError:
                detail = response.text
            raise ValueError(
                f"OpenAI embeddings request failed ({response.status_code}): {detail[:500]}"
            )
        data = response.json().get("data", [])
        embeddings = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if any(len(embedding) != self.dimension for embedding in embeddings):
            raise ValueError(
                f"Embedding provider returned "
                f"{len(embeddings[0]) if embeddings else 0} dimensions; "
                f"the database requires {self.dimension}"
            )
        return embeddings


class ExtractiveAnswerProvider:
    """Safe fallback that only composes retrieved evidence, never inventing claims."""

    def answer(self, query: str, context: str) -> str:
        if not context.strip():
            return (
                "I could not find sufficient supporting information in the uploaded documents."
            )
        excerpts = [line.strip() for line in context.splitlines() if line.strip()]
        return " ".join(excerpts[:6])


class OpenAIAnswerProvider:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def answer(self, query: str, context: str) -> str:
        system = (
            "Answer ONLY using the supplied retrieved evidence. If evidence contains the answer, provide it directly. "
            "If no evidence supports the answer, say exactly: I could not find sufficient supporting information in the uploaded documents. "
            "Do not invent policies, dates, entities, or numbers. Keep source markers such as [S1]."
        )
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Question: {query}\n\nEvidence:\n{context}"},
                ],
            },
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()


def embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.openai_api_key:
        return OpenAIEmbeddingProvider(
            settings.openai_api_key,
            settings.openai_embedding_model,
            settings.embedding_dimension,
        )
    return HashEmbeddingProvider(settings.embedding_dimension)


def answer_provider() -> AnswerProvider:
    settings = get_settings()
    if settings.openai_api_key:
        return OpenAIAnswerProvider(settings.openai_api_key, settings.openai_chat_model)
    return ExtractiveAnswerProvider()
