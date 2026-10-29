"""Research CGM samples, report summaries and timed insulin/meal events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'v19ae2b35d48'
down_revision = 'u08fd1a24c37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ck_glucose_source', 'glucose_readings', type_='check')
    op.create_check_constraint('ck_glucose_source', 'glucose_readings', "source IN ('manual','report','public_dataset','ohio_cgm')")
    op.create_table('study_subjects',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('dataset', sa.String(64), nullable=False),
        sa.Column('insulin_type', sa.String(128)),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('provenance', postgresql.JSONB(), nullable=False))
    op.create_table('study_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(16), nullable=False),
        sa.Column('detail', sa.String(128), nullable=False),
        sa.Column('source_ref', sa.String(255), nullable=False),
        sa.Column('raw_record', postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("kind IN ('bolus','basal','temp_basal','meal','finger_stick')", name='ck_study_event_kind'),
        sa.CheckConstraint('value >= 0', name='ck_study_event_value'),
        sa.CheckConstraint('ended_at IS NULL OR ended_at >= measured_at', name='ck_study_event_time'))
    op.create_index('ix_study_events_user_id', 'study_events', ['user_id'])
    op.create_index('ix_study_events_measured_at', 'study_events', ['measured_at'])


def downgrade() -> None:
    op.drop_table('study_events')
    op.drop_table('study_subjects')
    op.drop_constraint('ck_glucose_source', 'glucose_readings', type_='check')
    op.create_check_constraint('ck_glucose_source', 'glucose_readings', "source IN ('manual','report','public_dataset')")
