"""Loads a validated `import-dataset.json` into an empty catalogue as the seeded baseline (FR-70,
FR-76, ADR-0010, ADR-0042).

**One transaction, owned by the caller.** `seed_baseline` writes through the real write paths and
never commits. Any exception leaves the caller to roll back, so a refusal anywhere, including a
FR-05 collision on the 400th entry, leaves the database as it was. This matches the transform's own
"a blocking finding aborts emission entirely" discipline. `scripts/seed_baseline.py` commits once,
or rolls back for `--dry-run`.

**Through `create_entry` and its siblings, never a Core `insert()` or `COPY`.** The seeded rows
then satisfy FR-05 collision checks, FR-37 changelog notes and NFR-08 audit events by construction,
and `clean_term` runs on every preferred term, which FR-87's `char_length` report depends on.
`seed_system_properties` makes the same argument for its own rows. A whole-table check at the end
(`find_unclean_preferred_terms`) proves no row skipped that hook.

**Fails closed on a non-empty catalogue.** `business_key` numbering is positional (ADR-0010), so
mixing two runs' keys would attach keys to different clinical concepts without any error. A reseed
is a deliberate database reset, in the runbook.

**The audit append lock is taken first**, before the first row lock, as every multi-row writer
does (ADR-0035). `test_lock_ordering.py` scans only three modules; this one holds the order by
discipline.

**How the dataset maps onto the model.**

- The en-AU preferred term lives only in `catalogue_entry.preferred_term`
  (`ck_designation_no_en_au_preferred`), so the dataset's preferred designation is checked by the
  reader and not written again. Synonyms go through `add_synonyms`.
- Discipline labels resolve to the governed `discipline` local codes by display, ignoring case.
  A label with no match refuses the run: the vocabulary is RCPA-QAP's to extend.
- Subgroup labels become provisional local codes, verbatim, when no code exists yet (FR-92).
- Specimen values are stored with the SNOMED CT code the transform proved (FR-88). The reader has
  already refused any without one.
- `seed_system_properties` runs first, because nothing else creates the four system property
  definitions on a new deployment.

**No terminology lookups.** `--check-terminology` validated the codes before the dataset was
emitted, and re-validating is out of scope. The property registry still validates
every value's shape and, for a local code, its membership. Only the live value-set check is
answered by `_TrustedTerminologyClient`, which accepts any code and raises on every other call.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.bindings import create_binding
from nptc.catalogue.changelog import SEED_IMPORT_NOTE
from nptc.catalogue.designations import add_synonyms
from nptc.catalogue.entries import advance_sequence_past, create_entry
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup, create_local_code_unchecked
from nptc.catalogue.property_values import PropertyValueInput, save_property_values
from nptc.catalogue.seed_dataset import DatasetEntry, ImportDataset, highest_business_key
from nptc.catalogue.term_hygiene import clean_term
from nptc.db.bootstrap import seed_system_properties
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.entry_seed_provenance import EntrySeedProvenance
from nptc.db.models.local_code import LocalCode, LocalCodeStatus
from nptc.db.models.local_code_system import LocalCodeSystem, LocalCodeSystemStatus
from nptc.db.models.property_definition import PropertyDefinition
from nptc.db.models.seed_import import SeedImport
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc_shared.terminology import SNOMED_SYSTEM, Edition, ValidationResult

__all__ = [
    "CatalogueNotEmptyError",
    "SeedEntryError",
    "SeedImportError",
    "SeedPrerequisiteError",
    "SeedReport",
    "SeedVerificationError",
    "find_unclean_preferred_terms",
    "seed_baseline",
]

_DISCIPLINE = "discipline"
_SUBGROUP = "subgroup"
_SPECIMEN = "specimen"
_USAGE_GUIDANCE = "usage_guidance"
_SYSTEM_PROPERTY_KEYS = (_DISCIPLINE, _SUBGROUP, _SPECIMEN, _USAGE_GUIDANCE)

#: How many offending keys an error names before it summarises the rest.
_KEYS_SHOWN = 10


class SeedImportError(Exception):
    """Base class for every refusal by `seed_baseline`. The caller must roll back."""


class CatalogueNotEmptyError(SeedImportError):
    """The catalogue already holds an entry, or a seeding run is already recorded."""


class SeedPrerequisiteError(SeedImportError):
    """Something the dataset needs does not exist in the database. `problems` say what and how to
    fix it."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


class SeedEntryError(SeedImportError):
    """A write for one entry was refused, most often a FR-05 designation collision. `detail` is the
    domain error's message, or empty for a database error, whose text can carry connection or
    statement detail that does not belong in operator output (NFR-26)."""

    def __init__(self, entry: DatasetEntry, cause: Exception) -> None:
        self.business_key = entry.business_key
        self.sheet = entry.source.sheet
        self.row = entry.source.row
        self.cause_type = type(cause).__name__
        self.detail = "" if isinstance(cause, SQLAlchemyError) else str(cause)
        super().__init__(
            f"{entry.business_key} (sheet {entry.source.sheet!r}, row {entry.source.row}) was "
            f"refused: {self.cause_type}" + (f": {self.detail}" if self.detail else "")
        )


class SeedVerificationError(SeedImportError):
    """The post-import whole-table check found a preferred term `clean_term` would change."""


@dataclass(frozen=True)
class SeedReport:
    release_name: str
    source_filename: str
    source_sha256: str
    entries: int
    synonyms: int
    code_bindings: int
    property_values: int
    highest_business_key: str
    #: Subgroup labels this run created as provisional local codes (FR-92).
    provisional_subgroup_codes: tuple[str, ...]
    #: System property keys this run created because they did not exist yet.
    system_properties_created: tuple[str, ...]


class _TrustedTerminologyClient:
    """Answers the one live call the registry makes while validating a coded value, FR-10's
    value-set check, with "valid". See the module docstring for why that is correct here. Every
    other operation raises, so a change that makes the loader need one fails loudly."""

    def validate_code(
        self,
        code: str,
        *,
        edition: Edition,
        display: str | None = None,
        value_set_url: str | None = None,
    ) -> ValidationResult:
        return ValidationResult(code=code, result=True)

    def expand(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the seed loader makes no terminology calls beyond validate_code")

    def lookup(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the seed loader makes no terminology calls beyond validate_code")

    def subsumes(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the seed loader makes no terminology calls beyond validate_code")


def _registry(session: Session) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=_TrustedTerminologyClient(),
                local_code_lookup=DatabaseLocalCodeLookup(session),
            )
        )
    )


def _shown(items: Iterable[str]) -> str:
    listed = list(items)
    text = ", ".join(listed[:_KEYS_SHOWN])
    if len(listed) > _KEYS_SHOWN:
        text += f", ... ({len(listed) - _KEYS_SHOWN} more)"
    return text


def _assert_nothing_seeded(session: Session) -> None:
    if session.execute(select(CatalogueEntry.id).limit(1)).first() is not None:
        raise CatalogueNotEmptyError(
            "the catalogue already holds entries. The loader seeds an empty catalogue only: "
            "business keys are positional, so a second run could attach a key to a different "
            "concept. To reseed, reset the database first (see the seed-baseline runbook)"
        )
    if session.execute(select(SeedImport.id).limit(1)).first() is not None:
        raise CatalogueNotEmptyError("a baseline seeding run is already recorded")


def _load_system_with_codes(
    session: Session, key: str
) -> tuple[LocalCodeSystem, Sequence[LocalCode]]:
    system = session.execute(
        select(LocalCodeSystem).where(LocalCodeSystem.key == key)
    ).scalar_one_or_none()
    if system is None:
        raise SeedPrerequisiteError(
            [f"the {key!r} local code system does not exist - run `alembic upgrade head`"]
        )
    if system.status == str(LocalCodeSystemStatus.DEPRECATED):
        raise SeedPrerequisiteError([f"the {key!r} local code system is deprecated"])
    codes = session.execute(select(LocalCode).where(LocalCode.system_id == system.id)).scalars()
    return system, tuple(codes)


def _distinct_labels(dataset: ImportDataset, property_key: str) -> list[str]:
    """Labels in first-appearance order, so provisional codes are created deterministically."""
    seen: dict[str, None] = {}
    for entry in dataset.entries:
        for value in getattr(entry.properties, property_key):
            seen.setdefault(value.value, None)
    return list(seen)


def _coded_value(system_uri: str, code: str, display: str) -> dict[str, str]:
    return {"system": system_uri, "code": code, "display": display}


def _resolve_discipline(session: Session, dataset: ImportDataset) -> dict[str, dict[str, str]]:
    system, codes = _load_system_with_codes(session, _DISCIPLINE)
    by_display = {
        code.display.casefold(): code
        for code in codes
        if code.status == str(LocalCodeStatus.ACTIVE)
    }
    resolved: dict[str, dict[str, str]] = {}
    unknown: list[str] = []
    for label in _distinct_labels(dataset, _DISCIPLINE):
        match = by_display.get(label.casefold())
        if match is None:
            unknown.append(label)
        else:
            resolved[label] = _coded_value(system.uri, match.code, match.display)
    if unknown:
        raise SeedPrerequisiteError(
            [
                f"discipline {label!r} is not an active code in the 'discipline' local code "
                "system - correct it in the workbook, or have an administrator add the code"
                for label in unknown
            ]
        )
    return resolved


def _resolve_subgroup(
    session: Session, ctx: AuditContext, dataset: ImportDataset
) -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
    """Existing codes are reused; every other label becomes a provisional code, verbatim, awaiting
    RCPA-QAP's vocabulary decision (FR-92)."""
    system, codes = _load_system_with_codes(session, _SUBGROUP)
    by_code = {code.code: code for code in codes}
    resolved: dict[str, dict[str, str]] = {}
    created: list[str] = []
    for label in _distinct_labels(dataset, _SUBGROUP):
        existing = by_code.get(label)
        if existing is None:
            existing = create_local_code_unchecked(
                session,
                ctx,
                system=system,
                code=label,
                display=label,
                provisional=True,
                reason=SEED_IMPORT_NOTE,
            )
            created.append(label)
        resolved[label] = _coded_value(system.uri, existing.code, existing.display)
    return resolved, tuple(created)


def _require_property_definitions(session: Session) -> None:
    present = set(
        session.scalars(
            select(PropertyDefinition.key).where(PropertyDefinition.key.in_(_SYSTEM_PROPERTY_KEYS))
        )
    )
    missing = [key for key in _SYSTEM_PROPERTY_KEYS if key not in present]
    if missing:
        raise SeedPrerequisiteError(
            [f"property definition(s) missing after bootstrap: {', '.join(missing)}"]
        )


def _property_values(
    entry: DatasetEntry,
    discipline: dict[str, dict[str, str]],
    subgroup: dict[str, dict[str, str]],
) -> list[tuple[str, list[PropertyValueInput]]]:
    properties = entry.properties
    specimen = [
        PropertyValueInput(value=_coded_value(SNOMED_SYSTEM, code, value.value))
        for value in properties.specimen
        if (code := value.code) is not None
    ]
    guidance = properties.usage_guidance
    return [
        (
            _DISCIPLINE,
            [PropertyValueInput(value=discipline[v.value]) for v in properties.discipline],
        ),
        (_SUBGROUP, [PropertyValueInput(value=subgroup[v.value]) for v in properties.subgroup]),
        (_SPECIMEN, specimen),
        (
            _USAGE_GUIDANCE,
            [PropertyValueInput(value=guidance)] if guidance and guidance.strip() else [],
        ),
    ]


@dataclass
class _Tally:
    synonyms: int = 0
    code_bindings: int = 0
    property_values: int = 0


def _write_entry(
    session: Session,
    ctx: AuditContext,
    *,
    entry: DatasetEntry,
    seed_import: SeedImport,
    registry: DatatypeRegistry,
    discipline: dict[str, dict[str, str]],
    subgroup: dict[str, dict[str, str]],
    tally: _Tally,
) -> None:
    created = create_entry(
        session,
        ctx,
        preferred_term=entry.preferred_term,
        reason=SEED_IMPORT_NOTE,
        status=entry.status,
        specimen_unconstrained=entry.specimen_unconstrained,
        business_key=entry.business_key,
    )

    synonyms_by_language: dict[str, list[str]] = {}
    for designation in entry.designations:
        if designation.use == "synonym":
            synonyms_by_language.setdefault(designation.language, []).append(designation.term)
    for language, terms in synonyms_by_language.items():
        tally.synonyms += len(
            add_synonyms(
                session, ctx, entry=created, terms=terms, language=language, reason=SEED_IMPORT_NOTE
            )
        )

    for binding in entry.code_bindings:
        assert binding.fsn is not None  # the reader refuses a binding with no FSN
        create_binding(
            session,
            ctx,
            entry=created,
            code=binding.code,
            fsn=binding.fsn,
            au_preferred_term=binding.au_preferred_term,
            edition_hint=binding.edition_hint,
            system=binding.system,
            reason=SEED_IMPORT_NOTE,
        )
        tally.code_bindings += 1

    for property_key, values in _property_values(entry, discipline, subgroup):
        if not values:
            continue
        save_property_values(
            session,
            ctx,
            entry=created,
            property_key=property_key,
            values=values,
            reason=SEED_IMPORT_NOTE,
            registry=registry,
            expected_row_version=created.row_version,
        )
        tally.property_values += len(values)

    provenance = EntrySeedProvenance(
        entry_id=created.id,
        seed_import_id=seed_import.id,
        source_sheet=entry.source.sheet,
        source_row=entry.source.row,
        legacy_version=entry.source.legacy_version,
        legacy_history=entry.source.legacy_history,
    )
    session.add(provenance)
    record_change(
        session,
        ctx,
        action="entry_seed_provenance.created",
        instance=provenance,
        kind=ChangeKind.CREATED,
        reason=SEED_IMPORT_NOTE,
    )


def find_unclean_preferred_terms(session: Session) -> list[str]:
    """Business keys of every entry whose stored preferred term `clean_term` would change or
    refuse. Whole-table by definition. FR-87's `char_length` report equals FR-85's published length
    only for a term that passed through `clean_term`, and a Core `insert()` or `COPY` skips it, so
    this is the check a shared-container test cannot make."""
    unclean: list[str] = []
    rows = session.execute(select(CatalogueEntry.business_key, CatalogueEntry.preferred_term))
    for business_key, term in rows:
        try:
            if clean_term(term) != term:
                unclean.append(business_key)
        except ValueError:
            unclean.append(business_key)
    return unclean


def seed_baseline(
    session: Session, dataset: ImportDataset, *, ctx: AuditContext | None = None
) -> SeedReport:
    """Writes `dataset` into the empty catalogue behind `session` and returns what it wrote.

    Never commits. Raises a `SeedImportError` subclass on any refusal, after which the caller must
    roll back: part of the work may already be flushed. `session` must be at READ COMMITTED, which
    `nptc.db.session.get_engine` guarantees and `acquire_append_lock` checks.
    """
    audit = ctx if ctx is not None else AuditContext.system()
    acquire_append_lock(session)
    _assert_nothing_seeded(session)

    system_properties_created = tuple(seed_system_properties(session))
    _require_property_definitions(session)
    discipline = _resolve_discipline(session, dataset)
    subgroup, provisional_codes = _resolve_subgroup(session, audit, dataset)

    seed_import = SeedImport(
        release_name=dataset.baseline_release.name,
        release_note=dataset.baseline_release.note,
        source_filename=dataset.source.filename,
        source_sha256=dataset.source.sha256,
        dataset_schema_version=dataset.schema_version,
        entry_count=len(dataset.entries),
    )
    session.add(seed_import)
    record_change(
        session,
        audit,
        action="seed_import.created",
        instance=seed_import,
        kind=ChangeKind.CREATED,
        reason=SEED_IMPORT_NOTE,
    )

    registry = _registry(session)
    tally = _Tally()
    for entry in dataset.entries:
        try:
            _write_entry(
                session,
                audit,
                entry=entry,
                seed_import=seed_import,
                registry=registry,
                discipline=discipline,
                subgroup=subgroup,
                tally=tally,
            )
        except SeedImportError:
            raise
        except Exception as exc:
            raise SeedEntryError(entry, exc) from exc

    highest = highest_business_key(dataset)
    advance_sequence_past(session, highest)

    unclean = find_unclean_preferred_terms(session)
    if unclean:
        raise SeedVerificationError(
            f"{len(unclean)} preferred term(s) were stored without cleaning: {_shown(unclean)}"
        )

    return SeedReport(
        release_name=dataset.baseline_release.name,
        source_filename=dataset.source.filename,
        source_sha256=dataset.source.sha256,
        entries=len(dataset.entries),
        synonyms=tally.synonyms,
        code_bindings=tally.code_bindings,
        property_values=tally.property_values,
        highest_business_key=highest,
        provisional_subgroup_codes=provisional_codes,
        system_properties_created=system_properties_created,
    )
