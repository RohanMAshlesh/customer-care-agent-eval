"""Baseline schema.

The first release creates every table from the ORM metadata. Later schema
changes are written as explicit, reviewed migrations on top of this revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-28
"""

from alembic import op

from ccevals import models  # noqa: F401
from ccevals.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
