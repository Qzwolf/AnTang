"""Distinguish attributed public history from personal measurements."""
from alembic import op

revision = "u08fd1a24c37"
down_revision = "t97ec0913b26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint("ck_glucose_source", "glucose_readings", "source IN ('manual', 'report', 'public_dataset')")


def downgrade() -> None:
    # Fail rather than relabel or delete imported research records.
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint("ck_glucose_source", "glucose_readings", "source IN ('manual', 'report')")
