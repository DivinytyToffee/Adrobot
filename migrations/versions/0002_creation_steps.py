"""Persist creation parameters and checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("campaign_creations", sa.Column("request_id", sa.String(36)))
    op.create_index("ix_campaign_creations_request_id", "campaign_creations", ["request_id"], unique=True)
    for field in ("domain_id", "group_id", "traffic_source_id", "geo_stream_id", "offer_stream_id"):
        op.add_column("campaign_creations", sa.Column(field, sa.Integer()))

def downgrade():
    op.drop_index("ix_campaign_creations_request_id", table_name="campaign_creations")
    with op.batch_alter_table("campaign_creations") as batch:
        for field in ("request_id", "domain_id", "group_id", "traffic_source_id", "geo_stream_id", "offer_stream_id"):
            batch.drop_column(field)
