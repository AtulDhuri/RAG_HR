"""Persist canonical parsed representation and provenance."""

from collections.abc import Sequence

from alembic import op

revision = "0002_llamaparse_provenance"
down_revision = "0001_initial_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS structured_markdown TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS layout_metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS document_type VARCHAR(128) NOT NULL DEFAULT 'generic'")
    op.execute("ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS domain VARCHAR(128) NOT NULL DEFAULT 'general'")
    op.execute("ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS classifier_confidence FLOAT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE semantic_chunks ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.drop_column("semantic_chunks", "metadata_json")
    op.drop_column("document_versions", "classifier_confidence")
    op.drop_column("document_versions", "domain")
    op.drop_column("document_versions", "document_type")
    op.drop_column("document_versions", "layout_metadata")
    op.drop_column("document_versions", "structured_markdown")
