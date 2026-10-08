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
- **[Searching the audit log](searching-the-audit-log.md)** — finding who changed what and
  when, and exporting a filtered view. No other role can read the audit log.

### Reaching each screen

Open **Admin** in the site header to reach the administration home at `/admin`. It shows one
card for each administration screen. Select a card's title to open the screen.

| Card | Screen | Status |
|---|---|---|
| Catalogue administration | `/admin/catalogue` | Built |
| Property registry | `/admin/properties` | Built |
| Audit log | `/admin/audit` | Built |
| User administration | `/admin/users` | Planned |
| Validation findings | `/admin/validation` | Planned |
| Release administration | `/admin/releases` | Planned |
| Export configuration | `/admin/exports/config` | Planned |

A planned card opens a page that says the screen has not been built yet.

If you are signed in without the Administrator role, the home shows the notice "Your account
does not have the administrator role" and keeps every card, because the server decides what
you may do on each screen.

If you are an Administrator and have not finished the extra sign-in step, the notice reads
"This session does not include the administrator role" instead, because the platform leaves
the role out of your session until that step is done. Use **Verify now** in the banner at
the top of the page, then reload.

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
