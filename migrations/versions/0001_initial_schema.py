"""Create the initial knowledge intelligence schema.

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

from alembic import op

from app import models  # noqa: F401
from app.db import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
