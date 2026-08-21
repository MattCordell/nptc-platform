"""local_code_systems

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-21

Issue #56 (FR-90, FR-91, FR-92): Discipline and Subgroup as governed local
code systems owned by RCPA-QAP, plus an advisory, non-authoritative SNOMED
CT map for Discipline. See `nptc.db.models.local_code_system`,
`nptc.db.models.local_code` and `nptc.db.models.local_code_snomed_map` for
the full per-table reasoning.

Renumbered from `0010` to `0011` (originally cut before #196/issue #51's
`0010_property_definition_and_value.py` merged and claimed that number
first) - `down_revision` below now points at that migration rather than
`0009`.

The privilege grants live in this same migration, not a later
"permissions" one - see 0008's own docstring for the same
grants-live-with-the-table reasoning (ADR-0011).

Three tables, created in dependency order (`local_code_system` before
`local_code` before `local_code_snomed_map`) and dropped in the reverse
order on downgrade:

- `local_code_system`: the governed vocabulary itself (`key`/`uri`
  immutable once set).
- `local_code`: one member of a system. `code` is immutable per system
  (mirrors `code_binding.code`); `provisional` marks a value migrated
  verbatim ahead of RCPA-QAP settling its vocabulary (FR-92).
- `local_code_snomed_map`: the FR-91 advisory map. Reuses `code_binding`'s
  own `nptc_sctid_is_valid` function (already created by 0008) for its
  `code` column's `CHECK` - no new database function is needed.

**This is the first seeding migration in the repository.** The seed
values below are a module-level constant here, not imported from `src/`,
so a later refactor of the service layer cannot silently rewrite this
migration's history. Seeded content is exactly what PRD SS6.6's own
verification table determines:

- The `discipline` system, its six codes, and four advisory map rows
  (`Chemical pathology`, `Haematology` and `Immunopathology` at `exact`
  strength; `Microbiology` mapped twice, at `ambiguous` strength, to both
  `408454008` and `394820005` - PRD SS6.6 is explicit that collapsing this
  to one candidate would be the approximation FR-91 forbids).
- `Molecular` and `Serology` get **no map row** - PRD SS6.6 found no
  SNOMED concept that is a genuine match for either, and FR-91 requires
  that gap to stay visible rather than be papered over.
- The `subgroup` system is seeded with no codes at all - FR-92 assigns
  the vocabulary decision to RCPA-QAP; seeding a `subgroup` code here
  would be exactly the guessing-at-structure FR-92 forbids.

**These seed inserts deliberately bypass `nptc.catalogue.local_codes` and
its NFR-08 audit trail** - unlike a state-changing write made through the
running application (what NFR-08 actually governs), this is bootstrap
data written before any `app_user`/`Principal` exists to attribute it to,
the same posture `0001_extensions_and_app_role.py`'s `CREATE_APP_ROLE_SQL`
already takes for the role itself. A later change to this seed data (a
corrected URI, an added map row) is a new migration, reviewed the way any
schema change is, not a service-layer write.

**The seed `uri` values use the reserved `nptc.example.org` domain
(RFC 2606) as an explicit placeholder** - no real external namespace for
NPTC's own local code systems has been decided yet (see `deploy/
.env.example`: even `NPTC_FRONTEND_BASE_URL` is `localhost` pending a real
deployment). Replacing it is a follow-up issue, not a decision this
migration can make on the maintainer's behalf; `uri`'s `UNIQUE` constraint
means that follow-up is itself a data migration on a published
identifier; the earlier that happens (this migration is the identifier's
first appearance), the cheaper it is.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISCIPLINE_SYSTEM_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_SUBGROUP_SYSTEM_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

#: PRD SS6.6's six RCPA disciplines, in the order the PRD's own
#: verification table lists them. `code` values are stable slugs, not
#: taken from the source workbook's free-text casing.
_DISCIPLINE_CODES: tuple[dict[str, object], ...] = (
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
        "code": "chemical_pathology",
        "display": "Chemical pathology",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a2"),
        "code": "haematology",
        "display": "Haematology",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a3"),
        "code": "immunopathology",
        "display": "Immunopathology",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a4"),
        "code": "microbiology",
        "display": "Microbiology",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a5"),
        "code": "molecular",
        "display": "Molecular",
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000a6"),
        "code": "serology",
        "display": "Serology",
    },
)

#: PRD SS6.6's Discipline-to-SNOMED verification table, reproduced exactly
#: - see the module docstring for why `Microbiology` gets two rows and
#: `Molecular`/`Serology` get none.
_DISCIPLINE_MAP_ROWS: tuple[dict[str, object], ...] = (
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000b1"),
        "local_code_id": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
        "code": "394596001",
        "display": "Chemical pathology",
        "match_strength": "exact",
        "advisory_note": (
            "Advisory only, not a code_binding: PRD SS6.6 verification, exact match."
        ),
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
        "local_code_id": uuid.UUID("00000000-0000-0000-0000-0000000000a2"),
        "code": "394916005",
        "display": "Haematology (specialty)",
        "match_strength": "exact",
        "advisory_note": (
            "Advisory only, not a code_binding: PRD SS6.6 verification, exact match."
        ),
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000b3"),
        "local_code_id": uuid.UUID("00000000-0000-0000-0000-0000000000a3"),
        "code": "394598000",
        "display": "Immunopathology",
        "match_strength": "exact",
        "advisory_note": (
            "Advisory only, not a code_binding: PRD SS6.6 verification, exact match."
        ),
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000b4"),
        "local_code_id": uuid.UUID("00000000-0000-0000-0000-0000000000a4"),
        "code": "408454008",
        "display": "Clinical microbiology",
        "match_strength": "ambiguous",
        "advisory_note": (
            "Advisory only, not a code_binding: PRD SS6.6 verification found two "
            "candidates for Microbiology, neither named plainly 'Microbiology'. See "
            "also 394820005."
        ),
    },
    {
        "id": uuid.UUID("00000000-0000-0000-0000-0000000000b5"),
        "local_code_id": uuid.UUID("00000000-0000-0000-0000-0000000000a4"),
        "code": "394820005",
        "display": "Medical microbiology",
        "match_strength": "ambiguous",
        "advisory_note": (
            "Advisory only, not a code_binding: PRD SS6.6 verification found two "
            "candidates for Microbiology, neither named plainly 'Microbiology'. See "
            "also 408454008."
        ),
    },
    # Deliberately no rows for 'molecular' or 'serology' - PRD SS6.6 found
    # no genuine SNOMED match for either (FR-91: an honest gap, not an
    # approximate mapping).
)


def upgrade() -> None:
    op.create_table(
        "local_code_system",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{0,62}$'", name=op.f("ck_local_code_system_key")),
        sa.CheckConstraint(
            "length(btrim(uri)) > 0", name=op.f("ck_local_code_system_uri_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_local_code_system_title_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(owner)) > 0", name=op.f("ck_local_code_system_owner_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('active','deprecated')", name=op.f("ck_local_code_system_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_code_system")),
        sa.UniqueConstraint("key", name=op.f("uq_local_code_system_key")),
        sa.UniqueConstraint("uri", name=op.f("uq_local_code_system_uri")),
    )

    op.create_table(
        "local_code",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("system_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("display", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("provisional", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecation_reason", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(code)) > 0", name=op.f("ck_local_code_code_not_blank")),
        sa.CheckConstraint(
            "length(btrim(display)) > 0", name=op.f("ck_local_code_display_not_blank")
        ),
        sa.CheckConstraint("status IN ('active','deprecated')", name=op.f("ck_local_code_status")),
        sa.CheckConstraint(
            "(status = 'deprecated') = "
            "(deprecation_reason IS NOT NULL AND length(btrim(deprecation_reason)) > 0)",
            name=op.f("ck_local_code_deprecation_reason"),
        ),
        sa.CheckConstraint(
            "(status = 'deprecated') = (deprecated_at IS NOT NULL)",
            name=op.f("ck_local_code_deprecated_at"),
        ),
        sa.ForeignKeyConstraint(
            ["system_id"],
            ["local_code_system.id"],
            name=op.f("fk_local_code_system_id_local_code_system"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_code")),
    )
    op.create_index(op.f("ix_local_code_system_id"), "local_code", ["system_id"], unique=False)
    op.create_index(
        "uq_local_code_system_id_code", "local_code", ["system_id", "code"], unique=True
    )

    op.create_table(
        "local_code_snomed_map",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("local_code_id", sa.UUID(), nullable=False),
        sa.Column(
            "system",
            sa.Text(),
            server_default=sa.text("'http://snomed.info/sct'"),
            nullable=False,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("display", sa.Text(), nullable=False),
        sa.Column("match_strength", sa.Text(), nullable=False),
        sa.Column("advisory_note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(system)) > 0", name=op.f("ck_local_code_snomed_map_system_not_blank")
        ),
        sa.CheckConstraint("nptc_sctid_is_valid(code)", name=op.f("ck_local_code_snomed_map_code")),
        sa.CheckConstraint(
            "length(btrim(display)) > 0", name=op.f("ck_local_code_snomed_map_display_not_blank")
        ),
        sa.CheckConstraint(
            "match_strength IN ('exact','narrower','broader','ambiguous')",
            name=op.f("ck_local_code_snomed_map_match_strength"),
        ),
        sa.CheckConstraint(
            "length(btrim(advisory_note)) > 0",
            name=op.f("ck_local_code_snomed_map_advisory_note_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["local_code_id"],
            ["local_code.id"],
            name=op.f("fk_local_code_snomed_map_local_code_id_local_code"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_code_snomed_map")),
    )
    op.create_index(
        op.f("ix_local_code_snomed_map_local_code_id"),
        "local_code_snomed_map",
        ["local_code_id"],
        unique=False,
    )

    op.execute(roles.GRANT_LOCAL_CODE_SYSTEM_SQL)
    op.execute(roles.GRANT_LOCAL_CODE_SYSTEM_UPDATE_SQL)
    op.execute(roles.REVOKE_LOCAL_CODE_SYSTEM_DELETE_SQL)
    op.execute(roles.GRANT_LOCAL_CODE_SQL)
    op.execute(roles.GRANT_LOCAL_CODE_UPDATE_SQL)
    op.execute(roles.REVOKE_LOCAL_CODE_DELETE_SQL)
    op.execute(roles.GRANT_LOCAL_CODE_SNOMED_MAP_SQL)
    op.execute(roles.REVOKE_LOCAL_CODE_SNOMED_MAP_WRITE_SQL)

    _seed()


def _seed() -> None:
    local_code_system = sa.table(
        "local_code_system",
        sa.column("id", sa.UUID()),
        sa.column("key", sa.Text()),
        sa.column("uri", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("owner", sa.Text()),
    )
    local_code = sa.table(
        "local_code",
        sa.column("id", sa.UUID()),
        sa.column("system_id", sa.UUID()),
        sa.column("code", sa.Text()),
        sa.column("display", sa.Text()),
    )
    local_code_snomed_map = sa.table(
        "local_code_snomed_map",
        sa.column("id", sa.UUID()),
        sa.column("local_code_id", sa.UUID()),
        sa.column("code", sa.Text()),
        sa.column("display", sa.Text()),
        sa.column("match_strength", sa.Text()),
        sa.column("advisory_note", sa.Text()),
    )

    op.bulk_insert(
        local_code_system,
        [
            {
                "id": _DISCIPLINE_SYSTEM_ID,
                "key": "discipline",
                "uri": "https://nptc.example.org/CodeSystem/discipline",
                "title": "Discipline",
                "description": (
                    "RCPA pathology disciplines - not expressible as a single coherent "
                    "SNOMED CT value set (PRD SS6.6)."
                ),
                "owner": "RCPA-QAP",
            },
            {
                "id": _SUBGROUP_SYSTEM_ID,
                "key": "subgroup",
                "uri": "https://nptc.example.org/CodeSystem/subgroup",
                "title": "Subgroup",
                "description": (
                    "RCPA pathology subgroups - vocabulary not yet reconciled by "
                    "RCPA-QAP (FR-92); seeded with no codes."
                ),
                "owner": "RCPA-QAP",
            },
        ],
    )

    op.bulk_insert(
        local_code,
        [
            {
                "id": row["id"],
                "system_id": _DISCIPLINE_SYSTEM_ID,
                "code": row["code"],
                "display": row["display"],
            }
            for row in _DISCIPLINE_CODES
        ],
    )

    op.bulk_insert(
        local_code_snomed_map,
        [
            {
                "id": row["id"],
                "local_code_id": row["local_code_id"],
                "code": row["code"],
                "display": row["display"],
                "match_strength": row["match_strength"],
                "advisory_note": row["advisory_note"],
            }
            for row in _DISCIPLINE_MAP_ROWS
        ],
    )


def downgrade() -> None:
    op.drop_table("local_code_snomed_map")
    op.drop_table("local_code")
    op.drop_table("local_code_system")
