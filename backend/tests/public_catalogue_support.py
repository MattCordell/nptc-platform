"""Seed data for the FR-20 public API tests (issue #142).

Not a `test_*.py` module - imported by path via `importlib`, the same
convention as `api_app_support.py`/`authz_app_support.py`.

**One canonical entry carries the whole UI-parity criterion.** The issue's
acceptance criterion is that the API serves the same content as the public
UI, and the shapes most likely to be dropped by an incomplete
implementation are exactly the ones a simple fixture omits: an entry with
synonyms, an entry with *several* values for one property (so `ordinal`
means something), and an entry with a retired binding carrying both a
reason and a successor. `SeededCatalogue.canonical` has all three at once,
so a single request exercises every one of them and no test has to
remember to check them separately.

**Rows are built directly from the models, not through the write services.**
`nptc.catalogue.entries.create_entry` and friends carry domain rules of
their own (audit events, collision detection, a `draft` starting status)
that a *read* fixture would have to work around rather than benefit from -
notably, an entry cannot be born `active`, and three of the four statuses
these tests need to prove are hidden are ones no write path here would
produce. Constructing the models directly still runs every `@validates`
hook and every database constraint, which is the part that matters for
whether the fixture is realistic.

**Every seeded identifier carries a per-run token.** `business_key`s are
allocated from one contiguous, randomly-placed nine-digit block, and the
property keys carry the same token. Two consequences, both deliberate:
no assertion here ever depends on an absolute row count or on this module
being the only writer (CLAUDE.md's shared-container rule), and because the
block is contiguous, a paging test can pass `after=<block start - 1>` and
receive exactly this fixture's entries in a known order regardless of what
else is in the table. Nine digits also sort after the six-digit keys the
real minting sequence produces, so the block is at the end of the
`business_key` ordering rather than interleaved with it.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding, CodeBindingStatus
from nptc.db.models.designation import Designation, DesignationStatus, DesignationUse
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.property_value import PropertyValue

#: Real, Verhoeff-valid SCTIDs (the `code` column's `CHECK` calls
#: `nptc_sctid_is_valid`, so an invented number would not insert at all) -
#: the same two `test_catalogue_bindings.py` already uses, with their real
#: FSNs so `render_display_term`'s FR-83 strip has something honest to work
#: on. `391483001`'s FSN is PRD SS6.4's named double-strip regression case:
#: it carries two parenthesised groups and only the last is the semantic tag.
ACTIVE_CODE = "391483001"
ACTIVE_FSN = "Microscopy (acid fast bacilli) (procedure)"
ACTIVE_DISPLAY_TERM = "Microscopy (acid fast bacilli)"
RETIRED_CODE = "71388002"
RETIRED_FSN = "Procedure (procedure)"

#: The canonical entry's preferred term, and the accented entry's. Both are
#: what the search tests query against, so they live here rather than being
#: retyped in each test module.
CANONICAL_TERM = "Haemoglobin electrophoresis"
CANONICAL_SYNONYM = "Hb electrophoresis"
ACCENTED_TERM = "Müller cell antibody"
#: An entry whose preferred term shares nothing with the query - the only
#: way to reach it is through its synonym, which is what proves the
#: designation half of the search actually runs.
SYNONYM_ONLY_TERM = "Unrelated placeholder assay"
SYNONYM_ONLY_SYNONYM = "Haemoglobin electrophoresis by capillary method"
#: Two entries with the identical preferred term, so their trigram scores
#: against any query are equal to the bit. Without a `business_key`
#: tie-break in the keyset, a page boundary landing here drops or repeats a
#: row - see `test_api_public_search.py`.
TIE_TERM = "Reticulocyte count tie fixture"
#: A retired synonym on the canonical entry. Deliberately shares no word
#: with any other seeded term, so a query for it can only match by way of
#: the retired row itself - if it shared text with a live term the "retired
#: synonyms are not searchable" test would match through that instead and
#: prove nothing.
RETIRED_SYNONYM = "Obsolete zarquon panel"

#: Deliberately small. `test_api_public_response_hygiene.py` asserts no
#: unquoted six-or-more-digit number appears in any response body (FR-06),
#: and `ordinal`, `length`, `score` and this value are the only numbers
#: served - so this stays well under six digits, and a future fixture value
#: of `1000000` would (correctly) fail that test rather than weaken it.
VOLUME_VALUE = 5

SPECIMEN_VALUES = ("Whole blood", "EDTA blood")


@dataclass(frozen=True)
class SeededCatalogue:
    """Everything a test needs to address the seeded rows by name.

    No internal ids: the tests assert against a public API whose whole
    contract is that `business_key` is the only identifier (PRD SS6.2), and a
    fixture handing out UUIDs invites an assertion that quietly proves less
    than it looks like it does.
    """

    token: str
    #: Exclusive lower bound for a keyset page covering exactly this
    #: fixture's entries - pass as `?after=`.
    before_all: str
    canonical: str
    accented: str
    synonym_only: str
    tie_first: str
    tie_second: str
    draft: str
    deprecated: str
    withdrawn: str
    specimen_property_key: str
    volume_property_key: str

    @property
    def hidden(self) -> tuple[str, ...]:
        """The three statuses the public API must never serve, in one tuple
        so a parametrised test cannot quietly cover only two of them."""
        return (self.draft, self.deprecated, self.withdrawn)

    @property
    def active_in_key_order(self) -> tuple[str, ...]:
        """Every seeded `active` entry, in `business_key` order - which is
        the order `GET /catalogue/entries` must return them in."""
        return (
            self.canonical,
            self.accented,
            self.synonym_only,
            self.tie_first,
            self.tie_second,
        )


def _entry(business_key: str, preferred_term: str, status: str) -> CatalogueEntry:
    return CatalogueEntry(
        business_key=business_key,
        preferred_term=preferred_term,
        status=status,
    )


def seed_public_catalogue(session: Session) -> SeededCatalogue:
    """Inserts the fixture and flushes it, returning the handles.

    Flushed, not committed: the caller's connection is inside a transaction
    the `app_db`/`db` fixture rolls back, so nothing here survives the test.
    """
    # A nine-digit block, so these keys sort after every six-digit key the
    # real minting sequence produces and a `?after=` cursor can exclude
    # everything but this fixture.
    base = random.randrange(100_000_000, 999_000_000)
    token = str(base)

    def key(offset: int) -> str:
        return f"NPTC-{base + offset}"

    seeded = SeededCatalogue(
        token=token,
        before_all=f"NPTC-{base - 1}",
        canonical=key(0),
        accented=key(1),
        synonym_only=key(2),
        tie_first=key(3),
        tie_second=key(4),
        draft=key(5),
        deprecated=key(6),
        withdrawn=key(7),
        specimen_property_key=f"specimen_type_{token}",
        volume_property_key=f"volume_ml_{token}",
    )

    canonical = _entry(seeded.canonical, CANONICAL_TERM, CatalogueEntryStatus.ACTIVE.value)
    # FR-89: this entry states positively that it accepts any specimen,
    # which is not the same claim as having no specimen property recorded -
    # and it *also* has specimen values, so a response that conflated the
    # two would be visibly wrong here.
    canonical.specimen_unconstrained = True
    entries = [
        canonical,
        _entry(seeded.accented, ACCENTED_TERM, CatalogueEntryStatus.ACTIVE.value),
        _entry(seeded.synonym_only, SYNONYM_ONLY_TERM, CatalogueEntryStatus.ACTIVE.value),
        _entry(seeded.tie_first, TIE_TERM, CatalogueEntryStatus.ACTIVE.value),
        _entry(seeded.tie_second, TIE_TERM, CatalogueEntryStatus.ACTIVE.value),
        # The three hidden entries deliberately carry near-copies of the
        # canonical entry's own preferred term, so a search for that term
        # scores them well above the threshold. Without that, a search test
        # asserting they are absent would pass even with no status filter
        # at all - they simply would not have matched.
        _entry(seeded.draft, f"{CANONICAL_TERM} draft", CatalogueEntryStatus.DRAFT.value),
        _entry(
            seeded.deprecated,
            f"{CANONICAL_TERM} deprecated",
            CatalogueEntryStatus.DEPRECATED.value,
        ),
        _entry(
            seeded.withdrawn,
            f"{CANONICAL_TERM} withdrawn",
            CatalogueEntryStatus.WITHDRAWN.value,
        ),
    ]
    session.add_all(entries)
    # Flushed before the children so `canonical.id`/`synonym_only.id` are
    # real - `id` is a `server_default`, not client-generated.
    session.flush()

    synonym_only = entries[2]
    session.add_all(
        [
            Designation(
                entry_id=canonical.id,
                term=CANONICAL_SYNONYM,
                use=DesignationUse.SYNONYM.value,
                language="en-AU",
                status=DesignationStatus.ACTIVE.value,
            ),
            # A non-en-AU *preferred* variant: permitted (ADR-0022 forbids
            # only the en-AU one, which lives on the entry itself), and the
            # shape a response model that assumed `use == "synonym"` would
            # mishandle.
            Designation(
                entry_id=canonical.id,
                term="Hemoglobin electrophoresis",
                use=DesignationUse.PREFERRED.value,
                language="en-US",
                status=DesignationStatus.ACTIVE.value,
            ),
            # Retired: must not be served, and must not be matched by
            # search either - `ix_designation_term_trgm` is partial on
            # `status = 'active'` for exactly this reason.
            Designation(
                entry_id=canonical.id,
                term=RETIRED_SYNONYM,
                use=DesignationUse.SYNONYM.value,
                language="en-AU",
                status=DesignationStatus.RETIRED.value,
            ),
            Designation(
                entry_id=synonym_only.id,
                term=SYNONYM_ONLY_SYNONYM,
                use=DesignationUse.SYNONYM.value,
                language="en-AU",
                status=DesignationStatus.ACTIVE.value,
            ),
        ]
    )

    active_binding = CodeBinding(
        entry_id=canonical.id,
        code=ACTIVE_CODE,
        fsn=ACTIVE_FSN,
        au_preferred_term=ACTIVE_DISPLAY_TERM,
        edition_hint="au",
        status=CodeBindingStatus.ACTIVE.value,
    )
    session.add(active_binding)
    session.flush()
    # FR-08's replacement case: retired, with a reason (mandatory exactly
    # when retired) and a pointer to the binding that superseded it. The
    # successor here is the entry's own active binding, which is what a
    # real retire-and-rebind produces.
    session.add(
        CodeBinding(
            entry_id=canonical.id,
            code=RETIRED_CODE,
            fsn=RETIRED_FSN,
            au_preferred_term=None,
            edition_hint="int",
            status=CodeBindingStatus.RETIRED.value,
            retirement_reason="Concept inactivated in the July release; rebound to a successor.",
            replaced_by_binding_id=active_binding.id,
        )
    )

    session.add_all(
        [
            PropertyDefinition(
                key=seeded.specimen_property_key,
                label="Specimen type",
                datatype="string",
                # Multi-valued on purpose: `ordinal` is only meaningful, and
                # only assertable, for a property that can hold more than
                # one value.
                cardinality="0..*",
                scope="both",
                required_for_submission=False,
                required_for_publication=False,
                filterable=True,
                origin="admin",
                display_order=10,
            ),
            PropertyDefinition(
                key=seeded.volume_property_key,
                label="Minimum volume (mL)",
                datatype="positiveInt",
                cardinality="0..1",
                scope="both",
                required_for_submission=False,
                required_for_publication=False,
                filterable=False,
                origin="admin",
                display_order=20,
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            PropertyValue(
                entry_id=canonical.id,
                property_key=seeded.specimen_property_key,
                ordinal=ordinal,
                value=value,
            )
            for ordinal, value in enumerate(SPECIMEN_VALUES)
        ]
    )
    session.add(
        PropertyValue(
            entry_id=canonical.id,
            property_key=seeded.volume_property_key,
            ordinal=0,
            value=VOLUME_VALUE,
        )
    )
    session.flush()

    return seeded


#: A stored FSN with no trailing parenthesised group, so `render_display_term`
#: refuses it (FR-83's first defensive assertion). Not blank - `fsn` has a
#: `length(btrim(fsn)) > 0` CHECK - and deliberately a value that looks
#: entirely plausible, because that is what a real corrupted row looks like:
#: an already-stripped display term written back into the column.
CORRUPT_FSN = "Microscopy"


def corrupt_stored_fsn(session: Session, business_key: str) -> None:
    """Replaces the entry's active binding FSN with one that is not a served
    FSN (FR-82's guarantee broken), for the read-path failure case.

    Written with a Core `UPDATE` rather than by loading the model, because
    `code_binding` carries no `@validates` hook on `fsn` (it is stored exactly
    as served) and there is therefore nothing to bypass - the point is only to
    produce the row a corrupted catalogue would have.
    """
    entry_id = session.execute(
        select(CatalogueEntry.id).where(CatalogueEntry.business_key == business_key)
    ).scalar_one()
    session.execute(
        update(CodeBinding)
        .where(
            CodeBinding.entry_id == entry_id,
            CodeBinding.status == CodeBindingStatus.ACTIVE.value,
        )
        .values(fsn=CORRUPT_FSN)
    )
    session.flush()


def unused_business_key() -> str:
    """A well-formed `business_key` no entry holds - for the 404 case.

    Well-formed matters: a malformed key is a 422 from the path validator
    and never reaches a query at all, so it would not test the same thing.
    """
    return f"NPTC-{random.randrange(900_000_000, 999_999_999)}"


def a_uuid() -> str:
    """A UUID string, for the "a UUID in the path is a 422, not a lookup"
    case."""
    return str(uuid.uuid4())
