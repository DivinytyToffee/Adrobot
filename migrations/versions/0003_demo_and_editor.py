from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"

def upgrade():
    op.create_table("preferences", sa.Column("campaign_id", sa.Integer(), primary_key=True),
                    sa.Column("stream_id", sa.Integer(), primary_key=True), sa.Column("pinned", sa.Text(), nullable=False))
    op.create_table("objects", sa.Column("kind", sa.String(), primary_key=True),
                    sa.Column("id", sa.Integer(), primary_key=True), sa.Column("body", sa.Text(), nullable=False))
def downgrade():
    op.drop_table("preferences")
    op.drop_table("objects")
