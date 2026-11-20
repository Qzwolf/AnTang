"""Distinguish attributed public history from personal measurements.

The check is written as a superset rather than the narrow set this branch
originally used: personal readings already in production carry
``fingerstick``/``cgm``/``lab`` sources, so adding a narrowing constraint on top
of them fails with "ck_glucose_source ... is violated by some row". The final
value list is settled in the merge revision ``m2a3b4c5d6e7``.
"""
from alembic import op

revision = "u08fd1a24c37"
down_revision = "t97ec0913b26"
branch_labels = None
depends_on = None

_SOURCES = "'manual','fingerstick','cgm','lab','report','public_dataset'"


def upgrade() -> None:
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint(
        "ck_glucose_source", "glucose_readings", f"source IN ({_SOURCES})"
    )


def downgrade() -> None:
    # Fail rather than relabel or delete imported research records. Narrowing the
    # check here would also break on rows this branch never wrote, so keep it wide.
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint(
        "ck_glucose_source", "glucose_readings", f"source IN ({_SOURCES})"
    )
