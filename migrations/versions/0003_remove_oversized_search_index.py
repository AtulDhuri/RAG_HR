"""Remove the invalid B-tree index from unrestricted search text."""

from collections.abc import Sequence

from alembic import op

revision = "0003_no_search_index"
down_revision = "0002_llamaparse_provenance"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_search_text")


def downgrade() -> None:
    # Do not recreate a plain B-tree index for unrestricted long text.
    pass
