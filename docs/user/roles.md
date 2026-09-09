# Roles

What the Administrator and Reviewer roles can each do on the screens this platform has
shipped so far. Every other authenticated role (Provisional, Member) has no screen of its
own yet — see the PRD's permission matrix (§4.7) for what they will eventually reach.
Anonymous and Observer use is self-explanatory from the public catalogue itself: browsing,
searching and viewing what has already been published needs no account and no role
beyond it.

## Administrator

RCPA-QAP staff and their delegates. Administrators hold every capability this platform has
shipped:

- **[Finding and filtering entries for editing](finding-entries.md)** and
  **[Editing an entry](editing-an-entry.md)** — the full editing surface: terms, code
  bindings, and registry properties, each with its own guide
  ([terms](editing-designations.md), [code bindings](binding-a-code.md),
  [registry properties](editing-registry-properties.md)).
- **[Bulk reclassify](bulk-reclassify.md)** — setting one registry property to one value
  across a selection of entries in a single audited batch.

**Multi-factor authentication.** Every administrative action needs a second sign-in step
beyond a password — see [Signing in](signing-in.md#multi-factor-authentication) for when
it is asked for and what happens if you decline it partway through.

Everything an Administrator does is checked server-side against a permission you hold,
never against the Administrator role by name — so a capability withheld from a role never
depends on this platform's own UI to enforce it (see each guide's own "If something goes
wrong" section for what a missing permission looks like from inside a screen).

## Reviewer

A working group member with editorial authority over the submission pipeline described in
the PRD, but **not** over the published catalogue — the screens for the submission
pipeline itself (proposing, reviewing and approving amendments) have not shipped yet. On
the screens that have, a Reviewer's one capability is:

- **Acknowledging a possible duplicate term.** Opening
  [Editing an entry's terms](editing-designations.md#if-something-goes-wrong), a Reviewer
  can acknowledge a warning-severity term collision (the same synonym recorded on more
  than one entry) so it stops recurring on every save of the entry that holds it — see
  that guide's "Resolving overlapping terms" section for what acknowledging does and does
  not affect.

A Reviewer can open the same entry-editing screen an Administrator uses, but every other
action on it — saving a term, a code binding, or a registry property, and bulk
reclassify — is refused. This is not a screen-level lock: every save is checked
server-side against the `catalogue.edit_published` permission specifically, which the
Reviewer role does not hold (PRD §4.5's own boundary between Reviewer and Administrator).
Unlike the multi-factor step-up in [Signing in](signing-in.md#multi-factor-authentication),
there is nothing further to complete here — a Reviewer editing a published entry is a
capability the role does not carry, not a step left undone.
