"""Add indexes for fast vector and keyword retrieval."""

from collections.abc import Sequence

from alembic import op

revision = "0004_retrieval_indexes"
down_revision = "0003_no_search_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Expression GIN index keeps the existing search_text schema unchanged.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_chunks_search_text_fts
        ON semantic_chunks
        USING gin (to_tsvector('simple', search_text))
    """)
    # HNSW makes cosine vector retrieval fast as the chunk corpus grows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw
        ON chunk_embeddings
        USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_search_text_fts")
