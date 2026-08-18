# ADR-0018: Field-level audit diffing

**Status:** Accepted
**Date:** 2026-08-18

## Context

Issue #36/ADR-0017 landed the audit hash chain and `append_audit_event`, the sole sanctioned
insert into `audit_event`. It takes `before`/`after` JSONB payloads that **callers hand-build**.
PRD NFR-08 requires those be "field-level, not whole-record blobs", and PRD Section 16 requires
an edit to "appear correctly in the audit log with before and after values".

Today there is exactly one caller - `close_account` in `nptc.auth.identity` - which hand-rolls
both its payload *and* its own privacy policy. ADR-0017 records why: `audit_event` is
INSERT/SELECT-only, so anything written to `before` is permanent, and writing the real
pre-closure `username`/`display_name`/`organisation` would defeat NFR-17's pseudonymisation at
the very moment it is recorded. So `close_account` records the pre-closure `status` plus the
*names* of the fields being cleared, never their values - a careful, one-off convention rather
than a general rule anything else could reuse or be checked against.

`P1-SEQUENCING.md` puts the whole audit block before any write path exists, on purpose (PRD
Section 17.3: "Do not defer the audit log ... retrofitting means touching every write path, and
the result is always incomplete"). `backend/src/nptc/api/` is a lone `__init__.py`; there is no
FastAPI app or `APIRouter` anywhere in `backend/src`; the only ORM models are `AuditEvent`,
`User`, `UserIdentity`. The entities that will actually generate diffs arrive with #46+. So this
issue's job is to land the mechanism and its enforcement now, while there is one call site to
convert instead of twenty, and to make the mechanism structurally hard for #46+ to bypass.

**A mismatch in the issue's own requirement IDs, recorded rather than papered over:** the issue
cites NFR-11, which is authentication events logged by Keycloak in its own event store (realm
config, landed by #40) - nothing here advances it. It also cites NFR-35 (structured JSON
logging), which this issue does not touch either. The redaction tests are instead marked
`req("NFR-26")` (no secrets in the repo/image layers) - consistent with `test_keycloak_realm.py`'s
own banned-key test - with docstrings noting NFR-26's literal scope is repo/image secrets, so
the tests defend the adjacent claim without moving NFR-26 off `planned`.

## Decision

**Strict serialisation, split from the hash chain's total normalisation.**
`nptc.audit.serialisation.normalise_json_value` is the strict counterpart to
`nptc.audit.hashing._normalise`: both convert the same set of Python types into JSON-safe form
(handled in explicit order - `bool` before `int`, since `bool` is an `int` subclass; `str` and
`str` subclasses including `StrEnum` before a non-`str` `Enum`; `Decimal` to `str`, never
`float`; `SCTID` to its `str` value, FR-06 made explicit; `UUID`/`ipaddress` to `str`;
`datetime` to UTC with a fixed 6-digit microsecond field; `date`/`time` to `isoformat()`;
`Mapping`/`list`/`tuple` recurse), but `normalise_json_value` **raises**
`UnserialisableAuditValueError` on anything it doesn't recognise, a NaN/±Inf float, a non-`str`
mapping key, or nesting past a depth cap of 32 - where `hashing._normalise` must stay total
(see below) and falls back to `str(value)`.

Two rules matter only now that real content reaches `before`/`after`, not merely `status` and
fixed field-name strings: a `str` containing a NUL byte (`U+0000`) raises, since Postgres
`jsonb` cannot store one at all and ADR-0017 already flagged this as becoming live "once a
future caller puts real catalogue content through this path" - that caller is this issue; the
right rejection point is FR-74's entry-time prohibition, not a silent escape that would make the
audit record differ from what was written. And the depth cap raises loudly rather than risking
Python's recursion limit mid-transaction.

**Why split strict from total, rather than making `hashing._normalise` itself strict.**
`compute_entry_hash` also runs over rows read back *from* Postgres (the writer's own
write-time self-check, and `verify_chain`), where raising on unfamiliar content would turn a
verifiable chain into an unverifiable one - a wrong trade for a function whose only job is
producing a comparable digest. `normalise_json_value`'s callers, by contrast, are about to
write a *new*, permanent payload (NFR-09: no UPDATE, no DELETE) - silently stringifying an
unexpected value there is exactly the silent content loss NFR-08 exists to prevent. So:
one shared, type-specific core (`normalise_json_value`) handles leaf typing, and
`hashing._normalise` keeps its own `Mapping`/`list`/`tuple` recursion (rather than delegating the
whole structure), catching `UnserialisableAuditValueError`/`ValueError` and falling back to
`str(value)` **per leaf**, not per container - delegating the whole recursion would silently
stringify an entire container for one unrecognised nested value, discarding every sibling value
along with it, which is not what "total" is supposed to mean. Every type reachable before this
issue normalises identically (a `Decimal` already fell through to `str(value)` under the old
code) - proven by a golden-vector digest test added to `test_audit_hashing.py`, computed
independently from the literal pre-issue-#37 implementation rather than derived from the
refactored code (a value derived from the new code would prove nothing about whether the
refactor changed behaviour). The one exception, currently unreachable through any field this
codebase writes: a NaN/±Inf `float`, tolerated as-is before this issue, now falls back to `str()`
at the leaf instead.

**Both an allowlist and a deny-list, layered, in `nptc.audit.policy`.** A deny-list alone fails
open: the first credential-shaped column nobody pattern-matched leaks the moment a diff is
taken. An allowlist alone fails to copy-paste: nothing stops `password_hash` from being pasted
into a model's declared fields by someone who didn't think to check. Both, with the deny check
running at `AuditFieldPolicy` **construction** time, so a call site cannot even build a policy
that declares a credential-shaped field.

**Declared on the model, not a central registry.** A model declares three `ClassVar`s -
`__audit_fields__` (recorded in full), `__audit_withheld_fields__` (changed-by-name only, under a
reserved `_redacted` key), and `__audit_ignored_fields__` (never appears in a diff at all) - and
`policy_for` (cached) combines them with `sqlalchemy.inspect(model).columns.keys()`. This keeps
the dependency one-directional (`policy.py` never imports a concrete model) and makes the policy
visible right next to the columns it governs. A model with no `__audit_fields__` at all raises
`MissingAuditPolicyError` - fails closed. A model deliberately never diffed (`AuditEvent` itself -
diffing the log is circular) sets `__audit_fields__ = None` **and** a mandatory
`__audit_exempt_reason__`, so the exemption carries its justification in code rather than being
inferred from silence.

**Every real column must land in exactly one of `auditable`/`withheld`/`ignored`, or
`AuditFieldPolicy` refuses to construct at all.** An allowlist alone only proves the columns it
names are handled correctly - it says nothing about the columns it doesn't name. Without a
completeness check, a model could declare `__audit_fields__ = {"status"}` and leave every other
column silently un-audited, and a column added to an already-classified model later would
silently escape auditing by default rather than failing a test - exactly the gap the issue's
"cannot land without classifying every column" claim is supposed to close. `__audit_ignored_
fields__` makes "we looked at this column and decided it doesn't belong in a diff" (a primary
key, a server-maintained timestamp) an explicit, reviewable statement instead of an accident of
omission; `ignored` fields are not deny-list checked, since excluding a credential-shaped column
from ever appearing in a diff is exactly the right outcome for it.

Applied: `User` gets `auditable = {status, closed_at}`, `withheld = {username, display_name,
organisation}`, `ignored = {id, created_at, updated_at}` - this turns `close_account`'s own care
into a policy anything touching `User` must now honour, checked by a test, not merely
remembered. `UserIdentity` gets
`auditable = {email_verified}`, `withheld = {issuer, subject, email}`, `ignored = {id, user_id,
linked_at}` (`subject` is the OIDC `sub`, which NFR-04 says must never escape; `email` is PII) -
its emit sites (identity created on login, deleted on closure) are deferred to a follow-up issue
against #43/#44, since #37's scope is the mechanism, not retrofitting every future call site at
once.

**`diff_instance` reads SQLAlchemy's own attribute history, never a caller-supplied snapshot.**
A caller cannot forget or misremember the "before" - there is no `before=` parameter to omit,
and the value is what SQLAlchemy actually loaded, not a hand-copied approximation that can
drift. `state.attrs[key].load_history()` is used, deliberately not `.history`: the latter runs
passive and returns `HISTORY_BLANK` (reporting "no change") for an expired or unloaded
attribute, which would silently hide a genuine edit; `load_history()` issues the `SELECT` needed
to fetch the committed value first. An attribute nobody touched reports no history and is
therefore absent from both payloads entirely - "unchanged fields absent, not null-to-null" holds
structurally, not via a filtering step someone could skip. Equal-valued reassignment is dropped
by comparing old to new.

**`diff_snapshots` is a first-class second path, not an escape hatch.** #51's `PropertyValue` is
JSONB rather than columns, and a future bulk reclassify may materialise no ORM instance per row.
Designing this in now, alongside `diff_instance`, avoids a second, divergent diffing helper
appearing later; it re-checks every incoming key against the deny-list at runtime too, so a
hand-assembled dict cannot smuggle a denied key past a mapper-derived policy that never declared
it.

**A snapshot key present in only one of `before`/`after` on an `UPDATED` diff is refused, not
treated as null.** `diff_instance` never faces this ambiguity - SQLAlchemy's attribute history
always supplies both the old and new value for a touched attribute - but a hand-built snapshot
pair has no such guarantee. Silently substituting `None` for the missing side would record a
spurious null-to-value (or value-to-null) change for a field the caller never actually reported
on that side, which is exactly the kind of fabricated diff content this issue exists to prevent.
`diff_snapshots` raises `ValueError` instead, requiring the caller to include a field in both
mappings (even unchanged) or omit it from both.

**Every auditable/withheld column must declare `active_history=True`, and `policy_for` enforces
it.** SQLAlchemy only knows an attribute's *prior* value if it was already loaded before being
reassigned. Reassigning an attribute that is expired-but-not-yet-reloaded (e.g. after a prior
`session.expire_all()`/`commit()` in the same session, or simply an attribute nobody happened to
read yet) leaves `load_history()` with no committed value to report - the "before" silently comes
back `None` instead of the true prior value, which is exactly the kind of quiet content loss this
whole issue exists to prevent. `mapped_column(..., active_history=True)` fixes this: SQLAlchemy
loads the previous value before allowing the overwrite, so `load_history()` always has a real
`deleted` entry to report regardless of what was or wasn't already loaded. Discovered while
writing `test_audit_diffing.py`'s own expired-instance test (an in-memory reproduction, not a
hypothetical) - `policy_for` now refuses to resolve a policy over any declared column missing
this, rather than let the gap ship silently. `User`/`UserIdentity`'s auditable and withheld
columns all carry it; a future model's columns will fail this check the same way a
credential-shaped name fails the deny-list, at policy-construction time, not at review time.

**The honest limitation: history is cleared by `flush()`.** `record_change` computes the diff
*before* delegating to `append_audit_event` (which flushes internally), so the ordinary call
sequence is safe. But if the *caller* flushes first, "already flushed" becomes indistinguishable
from "nothing changed" for `UPDATED`/`DELETED`. Three mitigations, all landed together rather
than picking one: `record_change` raises `AuditNoOpError` on an empty diff, so the bug is loud
rather than a silently missing audit event; for `kind=CREATED` specifically (whose diff reads
current attribute values directly, not history, so an empty-diff check would not catch this
ordering bug) it additionally asserts the instance is still in `session.new`; and the
constraint is documented in both `diffing.py`'s and `recording.py`'s own module docstrings, not
only here.

**No-op emits nothing, and refuses loudly - not silently.** ADR-0017 already set the precedent
("the idempotent early-return path emits nothing, which is correct"). `record_change` raises
`AuditNoOpError` on an empty diff; a caller with a genuinely idempotent path (`close_account`'s
existing early return) short-circuits *before* reaching the audit layer, exactly as it already
did. No lenient `record_change_if_any` variant is added: reaching `record_change` is meant to
assert a write happened, so an empty diff is always a bug. Adding a lenient variant later, if a
real caller needs one, is a small and reviewable act - a lenient default from day one is not.

**`append_audit_event`'s signature is unchanged.** It stays the general primitive: a future
`release.published` or NFR-12's `audit.exported` legitimately has no diff at all, and
`close_account` already proved a diff-free payload is sometimes exactly right. `record_change`/
`record_snapshot_change` are additive wrappers, not a replacement.

**Enforcement is structural, not a review checklist.** `test_audit_redaction.py` walks
`Base.registry`'s mappers and asserts every mapped class either resolves a policy or carries an
explicit `__audit_exempt_reason__` - this is the guard that actually bites: #46's
`catalogue_entry` cannot land without classifying every column, and cannot classify a
credential-shaped one as auditable. A third `ast` rule, `audit-diff-bypass`, extends
`test_audit_write_path_guard.py`: outside `nptc.audit` itself, a call to `append_audit_event`
carrying a `before=`/`after=` keyword whose value is not the literal `None` is a violation.
Deliberately narrower than "no `append_audit_event` outside `nptc.audit`" - a diff-free event is
legitimate and must stay directly callable; what is not legitimate is hand-building a diff
instead of going through `record_change`/`record_snapshot_change`, which is exactly the
per-endpoint reimplementation this issue exists to close off.

**`close_account` becomes the first real consumer.** Its hand-rolled `before_state`/`after_state`
dicts are deleted; the body becomes a single `record_change(session, audit, action="user.closed",
instance=user, kind=ChangeKind.UPDATED)` call. The early return for an already-closed account is
unchanged - it never reaches the audit layer at all, which is precisely the pattern
`record_change`'s loud refusal on an empty diff assumes.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Make `hashing._normalise` itself strict, rather than splitting it from `normalise_json_value` | It also runs over rows read back from Postgres (the writer's self-check, `verify_chain`); raising there would turn a verifiable chain into an unverifiable one. |
| A deny-list alone for field policy | The first credential-shaped column nobody pattern-matched leaks into a diff - fails open. |
| An allowlist alone for field policy | Nothing stops a credential-shaped name from being pasted into the allowlist by someone who didn't think to check - fails to copy-paste. |
| A central field-policy registry, separate from the model | Would need the registry to import every model (or vice versa, a cycle); declaring the policy on the model keeps it visible next to the columns it governs and needs no registry to stay in sync. |
| Caller-supplied `before=`/`after=` snapshots as the only diffing path (status quo) | Exactly the gap this issue exists to close - a caller can forget the "before", hand-copy a stale approximation, or omit a changed field entirely, with nothing to catch it. |
| `diff_instance` using `.history` instead of `.load_history()` | `.history` runs passive and returns `HISTORY_BLANK` (reporting "no change") for an expired or unloaded attribute - silently hides a genuine edit on exactly the kind of instance a real request handler often has. |
| A lenient `record_change_if_any` that swallows an empty diff | Reaching `record_change` is meant to assert a write happened; a lenient default from day one invites exactly the silent-no-audit-event bug NFR-08 exists to prevent. Adding it later, if ever needed, is a small reviewable act. |
| A blanket ban on `append_audit_event` outside `nptc.audit` (rather than the narrower `audit-diff-bypass` rule) | Would block a legitimate diff-free event (a future `release.published`, NFR-12's `audit.exported`) that has nothing to diff and should not be forced through a diffing path that does not apply to it. |
| Silently escaping a NUL byte (`nptc_shared.text.escape_invisible`) rather than raising | Would make the stored audit record differ from what was actually written - FR-74's entry-time prohibition is the correct place to reject this content, not a lossy repair inside the audit layer. |

## Consequences

- NFR-08 stays `in-progress`: `record_change` is now the diff-aware entry point and the
  model-coverage guard is what makes #46+ unable to bypass it, but there are still zero
  endpoints and so not yet "every state-changing write path".
- FR-06 stays `in-progress`; notes gain the audit-diff normalisation (`SCTID` to its `str`
  value, never a bare int).
- NFR-26 stays `planned`; notes gain the deny-list + allowlist description. Its literal scope
  (no secrets in the repo/image layers) is not fully satisfied by this issue - the redaction
  tests defend the adjacent claim, as noted in Context above.
- `nptc.audit.diffing`'s flush-ordering limitation (history cleared by `flush()`) is a
  documented, mitigated constraint, not a closed gap: a caller that flushes before calling
  `record_change` gets a loud `AuditNoOpError` for `UPDATED`/`DELETED`, and a loud
  `AuditNoOpError` from the explicit `session.new` check for `CREATED` - never a silently
  missing event, but also never something this issue can prevent a caller from attempting in
  the first place.
- `UserIdentity`'s missing emit sites (identity created on login, identity deleted on closure)
  are an explicit follow-up issue against #43/#44 - the policy exists now so those sites cannot
  bypass it once they land.
- Route-level endpoint enumeration (a walk asserting every `APIRouter` write path calls
  `record_change`/`record_snapshot_change`) is noted on issue #37 as landing with the API layer
  (#142/#149/#150) - a route walk today would run over the empty set, since there is no
  `APIRouter` anywhere in `backend/src` yet.
- #46+ (the first real catalogue write paths) builds directly on `nptc.audit.policy` and
  `nptc.audit.recording`; a future bulk-reclassify or `PropertyValue` write path builds on
  `diff_snapshots` rather than needing a new diffing helper.
