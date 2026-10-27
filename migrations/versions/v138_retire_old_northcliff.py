"""Remove the explicitly retired MF001 branch, with a recoverable row snapshot."""
import json
from datetime import date, datetime
from decimal import Decimal
from alembic import op
import sqlalchemy as sa

revision = 'v138_retire_old_northcliff'
down_revision = 'v137_wjm_owner_profiles'
branch_labels = None
depends_on = None
BACKUP_REASON = 'User approved removal of inactive old Northcliff MF001'
TABLES = ('franchises', 'attendance_offices', 'royalty_scales', 'user_franchises')


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'franchise_retirement_backups' not in inspector.get_table_names():
        op.create_table('franchise_retirement_backups',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('original_franchise_id', sa.Integer(), nullable=False),
            sa.Column('reason', sa.String(255), nullable=False),
            sa.Column('snapshot_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))
    metadata = sa.MetaData()
    branch = sa.Table('franchises', metadata, autoload_with=bind, resolve_fks=False)
    old = bind.execute(sa.select(branch).where(branch.c.id == 2,
        sa.func.lower(branch.c.business_name) == "martin's funerals northcliff",
        branch.c.franchise_code == 'MF001', branch.c.is_performance_active.is_(False)).with_for_update()).mappings().first()
    if old is None:
        return
    active = bind.execute(sa.select(branch.c.id).where(branch.c.franchise_code == 'NORTHCLIFF', branch.c.is_performance_active.is_(True))).scalars().all()
    if len(active) != 1:
        raise RuntimeError('Verify the separate active Northcliff record before retiring MF001.')
    # Production was inspected: only the office, scales and three user links
    # belong to this obsolete record. Refuse deletion if financial/new data has
    # appeared since that check. The row lock also blocks new FK references.
    for name in inspector.get_table_names():
        if name in TABLES:
            continue
        for foreign_key in inspector.get_foreign_keys(name):
            if foreign_key['referred_table'] == 'franchises' and len(foreign_key['constrained_columns']) == 1:
                table = sa.Table(name, metadata, autoload_with=bind, resolve_fks=False)
                column = table.c[foreign_key['constrained_columns'][0]]
                if bind.execute(sa.select(sa.func.count()).select_from(table).where(column == old['id'])).scalar():
                    raise RuntimeError(f'New data exists for retired Northcliff in {name}; review it before removal.')
    records = {'franchises': [dict(old)]}
    for name in TABLES[1:]:
        if name not in inspector.get_table_names():
            records[name] = []
            continue
        table = sa.Table(name, metadata, autoload_with=bind, resolve_fks=False)
        records[name] = [dict(row) for row in bind.execute(sa.select(table).where(table.c.franchise_id == old['id'])).mappings()]
    backup = sa.Table('franchise_retirement_backups', metadata, autoload_with=bind)
    bind.execute(backup.insert().values(original_franchise_id=old['id'], reason=BACKUP_REASON, snapshot_json=json.dumps(records, default=str)))
    for name in reversed(TABLES[1:]):
        if name in metadata.tables:
            table = metadata.tables[name]
            bind.execute(table.delete().where(table.c.franchise_id == old['id']))
    bind.execute(branch.delete().where(branch.c.id == old['id']))


def downgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    backup = sa.Table('franchise_retirement_backups', metadata, autoload_with=bind)
    saved = bind.execute(sa.select(backup).where(backup.c.original_franchise_id == 2, backup.c.reason == BACKUP_REASON).order_by(backup.c.id.desc())).mappings().first()
    if saved:
        records = json.loads(saved['snapshot_json'])
        for name in TABLES:
            if not records[name]:
                continue
            table = sa.Table(name, metadata, autoload_with=bind, resolve_fks=False)
            for row in records[name]:
                for column in table.columns:
                    value = row.get(column.name)
                    if value is None:
                        continue
                    if isinstance(column.type, sa.DateTime):
                        row[column.name] = datetime.fromisoformat(value)
                    elif isinstance(column.type, sa.Date):
                        row[column.name] = date.fromisoformat(value)
                    elif isinstance(column.type, sa.Numeric):
                        row[column.name] = Decimal(str(value))
                bind.execute(table.insert().values(**row))
    op.drop_table('franchise_retirement_backups')
