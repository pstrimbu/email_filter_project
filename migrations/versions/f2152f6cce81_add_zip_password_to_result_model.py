"""Add zip_password to Result model

Revision ID: f2152f6cce81
Revises: d1e76d5b25d4
Create Date: 2024-11-12 10:21:17.467626

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2152f6cce81'
down_revision = 'd1e76d5b25d4'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('result', schema=None) as batch_op:
        batch_op.add_column(sa.Column('zip_password', sa.String(length=255), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('result', schema=None) as batch_op:
        batch_op.drop_column('zip_password')

    # ### end Alembic commands ###