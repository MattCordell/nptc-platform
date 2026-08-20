"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Issue #NNN (FR-nn): <FILL IN - which issue and FR/NFR this migration lands>.

<FILL IN - why, not what: the invariants this DDL enforces and the shape rejected
instead. This docstring is the primary, most detailed account (see CONTRIBUTING.md's
"A schema change's prose has one home each"). Schema shape belongs in data-model.md; an
operator-facing consequence (a precondition, a manual step, a non-obvious downgrade
order) belongs in upgrade.md, linking back here rather than restating this reasoning.
Delete this paragraph once the real rationale is written.>

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
