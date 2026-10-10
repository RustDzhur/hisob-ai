"""currencies, organization currencies, units of measure, catalog permissions

Revision ID: 0003
Revises: 0002
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CUR_ORG = "nullif(current_setting('app.current_org', true), '')::uuid"

# Реальные коды ISO 4217; у всех шесть валют по 2 знака после запятой.
CURRENCIES = [
    ("UZS", 2, "Oʻzbekiston soʻmi", "Узбекский сум", "Uzbekistani som"),
    ("USD", 2, "AQSh dollari", "Доллар США", "US dollar"),
    ("EUR", 2, "Yevro", "Евро", "Euro"),
    ("RUB", 2, "Rossiya rubli", "Российский рубль", "Russian ruble"),
    ("KZT", 2, "Qozogʻiston tengesi", "Казахстанский тенге", "Kazakhstani tenge"),
    ("CNY", 2, "Xitoy yuani", "Китайский юань", "Chinese yuan"),
]
# Системные единицы без официальных кодов классификатора (official_code остаётся пустым).
UNITS = [
    ("pcs", "dona", "шт", "pcs"),
    ("kg", "kg", "кг", "kg"),
    ("g", "g", "г", "g"),
    ("l", "l", "л", "l"),
    ("m", "m", "м", "m"),
]
NEW_PERMISSIONS = {
    "catalog.read": "Просмотр справочников",
    "catalog.manage": "Управление справочниками",
    "warehouses.read": "Просмотр складов",
    "warehouses.manage": "Управление складами",
}
# Матрица docs/06-stage2-plan.md §4 (предложение, Q15 ждёт подтверждения).
ROLE_PERMS = {
    "owner": list(NEW_PERMISSIONS),
    "org_admin": list(NEW_PERMISSIONS),
    "accountant": ["catalog.read", "catalog.manage", "warehouses.read"],
    "storekeeper": ["catalog.read", "warehouses.read", "warehouses.manage"],
    "sales_purchase_manager": ["catalog.read", "catalog.manage", "warehouses.read"],
    "document_operator": ["catalog.read", "warehouses.read"],
    "auditor": ["catalog.read", "warehouses.read"],
}


def upgrade() -> None:
    op.create_table(
        "currency",
        sa.Column("code", sa.String(3), primary_key=True),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("name_uz", sa.Text(), nullable=False),
        sa.Column("name_ru", sa.Text(), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
    )
    cur = sa.table(
        "currency",
        sa.column("code", sa.String),
        sa.column("minor_units", sa.Integer),
        sa.column("name_uz", sa.Text),
        sa.column("name_ru", sa.Text),
        sa.column("name_en", sa.Text),
    )
    op.bulk_insert(
        cur,
        [
            dict(code=c, minor_units=m, name_uz=u, name_ru=r, name_en=e)
            for c, m, u, r, e in CURRENCIES
        ],
    )

    op.create_table(
        "organization_currency",
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey("currency.code"), primary_key=True),
        sa.Column("is_base", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    # Уже существующие организации: базовая валюта включается автоматически.
    op.execute(
        "INSERT INTO organization_currency (organization_id, currency_code, is_base, enabled) "
        "SELECT o.id, o.base_currency, true, true FROM organization o "
        "JOIN currency c ON c.code = o.base_currency"
    )
    op.execute("ALTER TABLE organization_currency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organization_currency FORCE ROW LEVEL SECURITY")
    for cmd, clause in (
        ("SELECT", f"USING (organization_id = {CUR_ORG})"),
        ("INSERT", f"WITH CHECK (organization_id = {CUR_ORG})"),
        ("UPDATE", f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"),
    ):
        op.execute(
            f"CREATE POLICY org_currency_{cmd.lower()} ON organization_currency FOR {cmd} {clause}"
        )

    op.create_table(
        "unit_of_measure",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id")),
        sa.Column("code", CITEXT(), nullable=False),
        sa.Column("name_uz", sa.Text()),
        sa.Column("name_ru", sa.Text()),
        sa.Column("name_en", sa.Text()),
        sa.Column("official_code", sa.Text()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "name_uz IS NOT NULL OR name_ru IS NOT NULL OR name_en IS NOT NULL",
            name="ck_unit_of_measure_has_name",
        ),
    )
    op.create_index("uq_unit_org_code", "unit_of_measure", ["organization_id", "code"], unique=True)
    # системные коды (organization_id IS NULL) тоже уникальны
    op.execute(
        "CREATE UNIQUE INDEX uq_unit_system_code ON unit_of_measure (code) WHERE organization_id IS NULL"
    )
    unit = sa.table(
        "unit_of_measure",
        sa.column("id", sa.Uuid),
        sa.column("code", sa.Text),
        sa.column("name_uz", sa.Text),
        sa.column("name_ru", sa.Text),
        sa.column("name_en", sa.Text),
    )
    op.bulk_insert(
        unit,
        [dict(id=uuid.uuid4(), code=c, name_uz=u, name_ru=r, name_en=e) for c, u, r, e in UNITS],
    )
    op.execute("ALTER TABLE unit_of_measure ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE unit_of_measure FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY unit_select ON unit_of_measure FOR SELECT "
        f"USING (organization_id IS NULL OR organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY unit_insert ON unit_of_measure FOR INSERT WITH CHECK (organization_id = {CUR_ORG})"
    )
    op.execute(
        f"CREATE POLICY unit_update ON unit_of_measure FOR UPDATE "
        f"USING (organization_id = {CUR_ORG}) WITH CHECK (organization_id = {CUR_ORG})"
    )

    # права и их состав по ролям
    perm = sa.table("permission", sa.column("code", sa.Text), sa.column("description", sa.Text))
    op.bulk_insert(perm, [dict(code=c, description=d) for c, d in NEW_PERMISSIONS.items()])
    for role_code, perms in ROLE_PERMS.items():
        for p in perms:
            op.execute(
                sa.text(
                    "INSERT INTO role_permission (role_id, permission_code) "
                    "SELECT id, :p FROM role WHERE code = :r"
                ).bindparams(p=p, r=role_code)
            )

    op.execute("GRANT SELECT ON currency TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON organization_currency TO hisob_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON unit_of_measure TO hisob_app")


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permission WHERE permission_code IN ('catalog.read','catalog.manage','warehouses.read','warehouses.manage')"
    )
    op.execute(
        "DELETE FROM permission WHERE code IN ('catalog.read','catalog.manage','warehouses.read','warehouses.manage')"
    )
    for t in ("unit_of_measure", "organization_currency", "currency"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
