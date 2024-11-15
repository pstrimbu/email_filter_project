"""remove email_count

Revision ID: dbe375a178f5
Revises: 34e28bf03043
Create Date: 2024-11-07 10:44:21.803042

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'dbe375a178f5'
down_revision = '34e28bf03043'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('email_address', schema=None) as batch_op:
        batch_op.drop_column('email_count')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('email_address', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_count', mysql.INTEGER(), autoincrement=False, nullable=False))

    # ### end Alembic commands ###
