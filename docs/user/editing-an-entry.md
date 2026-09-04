# Editing an entry

The catalogue entry editing screen is one page with three sections: **Terms**, **Code
bindings**, and **Registry properties**. This page orients you across all three; each has
its own guide with the full detail.

You need the Administrator role to change any of them. Acknowledging a possible duplicate
term is the one exception — that needs only the Reviewer role.

Open an entry for editing at **Administration → Catalogue → Edit**, or go straight to
`/admin/catalogue/NPTC-000247/edit` for the entry you want. Entries that have not been
published yet can be edited the same way as published ones.

> **Multi-factor authentication is not yet prompted for automatically.** Changing the
> catalogue requires an administrator account that has completed the second sign-in step.
> If you have not, this screen refuses to load and tells you to sign in again. Until the
> automatic prompt lands, sign out and sign in again, completing the second step, then
> return here. See the follow-up issue linked from
> [ADR-0021](../adr/0021-browser-side-pkce-login.md).

## The three sections

- **[Editing an entry's terms](editing-designations.md)** — the preferred term and its
  synonyms: adding, editing and retiring them, and what happens when a term is already in
  use somewhere else.
- **[Binding a SNOMED CT code](binding-a-code.md)** — the entry's active code binding, and
  its retired ones: binding, retiring and replacing a code, and what to do when the
  terminology server cannot answer.
- **[Editing registry properties](editing-registry-properties.md)** — every value the
  registry's property definitions offer for this entry, plus the entry's own **accepts any
  specimen** setting.

Each section saves independently. Editing a term does not touch code bindings or
registry properties, and the other way around — every save is its own request, with its own
changelog note, against whichever part of the entry it changes.

## What is common to all three

**Every change needs a changelog note.** It becomes the published History text for the
entry, so write a sentence describing the change. Single words like "update" or "fix" are
refused, and the save button stays unavailable — telling you why, next to the note itself —
until you write one that passes, rather than only reporting it after you try to save. This
applies section by section: the note you write for a term has no bearing on a code binding
or a property value, even when you are changing more than one in the same visit.

**Every save is checked against what is already there.** If someone else changed the entry
while you had it open, your save is refused, you are told who changed it and what moved,
and the screen reloads their change for you. Nothing of yours is saved — check it is still
needed, then save it again. Not every write path checks this the same way yet: see each
section's own guide for what it covers today.

**Nothing is ever silently deleted.** Retiring a term or a code binding keeps it, with its
history, and stops it from being published. The catalogue's history for the entry — Who,
When, What, Why — is what every changelog note across all three sections builds.

**Computed figures are never inputs.** The preferred term's published character count, and
anything else the catalogue works out for you rather than asking you to type, has no field
of its own anywhere on this screen — see the "What you see" section of each guide for the
specific figures.

## If something goes wrong

Each guide has its own "If something goes wrong" section covering the refusals specific to
that part of the entry. Two apply across all three sections:

**"You cannot edit this entry with your current sign-in."** See the note on multi-factor
authentication at the top of this page.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. What you see may no longer be current. Sign in again and reopen the entry
before making further changes.
