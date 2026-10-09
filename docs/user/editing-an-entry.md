# Editing an entry

The catalogue entry editing screen has three parts. A **form** at the top edits the
preferred term and every registry property. The **Terms** section below it holds the
synonyms. The **Code bindings** section holds the SNOMED CT code. This page orients you
across all three; each has its own guide with the full detail.

You need the Administrator role to change any of them. Acknowledging a possible duplicate
term is the one exception. That needs only the Reviewer role.

Open an entry for editing from **[Administration → Catalogue](finding-entries.md)**, or go
straight to `/admin/catalogue/NPTC-000247/edit` for the entry you want. Entries that have not
been published yet can be edited the same way as published ones.

## The three parts

- **The form** — the entry's identifier, its **RCPA Preferred** term, and every registry
  property. See [Editing registry properties](editing-registry-properties.md) for the
  properties and [the form's save](#how-the-form-saves) below for how saving works.
- **[Editing an entry's terms](editing-designations.md)** — the synonyms: adding, editing
  and retiring them, and what happens when a term is already in use somewhere else.
- **[Binding a SNOMED CT code](binding-a-code.md)** — the entry's active code binding, and
  its retired ones: binding, retiring and replacing a code, and what to do when the
  terminology server cannot answer.

The form saves as one. The Terms and Code bindings sections still save on their own, each
with its own changelog note.

## The form

The identifier, for example `NPTC-000006`, is shown as text. The platform creates it and it
never changes.

**RCPA Preferred** is the catalogue's own name for the test. Under the box you see its
length in characters. The length updates as you type. It is counted the way the catalogue
counts it, after the spacing is cleaned, so a trailing non-breaking space does not add to it.
After you save, the form also shows the length the server counted.

Below that is one control for every registry property, in the registry's own order.

Write **one changelog note** at the bottom, then choose **Save**.

## How the form saves

Save sends only the fields you changed, one at a time: the preferred term first, then each
property. All of them share your changelog note.

- **A field the server refuses.** The field is marked with the reason, and it keeps what you
  typed. The fields after it are still saved.
- **A stale entry, a sign-in problem or a server failure.** The save stops, because the
  fields after it would fail for the same reason. Fields not yet sent stay on screen.
- **Afterwards.** A summary lists the fields that saved, the fields that did not and why.
  Screen readers announce it too. If something did not save, focus moves to the summary.

To retry, fix the marked fields and choose **Save** again. Only fields that are not yet
saved are sent.

## What is common to all three parts

**Every change needs a changelog note.** It becomes the published History text for the
entry, so write a sentence describing the change. Single words like "update" or "fix" are
refused, and the save button stays unavailable — telling you why, next to the note itself —
until you write one that passes. The form has one note for everything it saves. The Terms
and Code bindings sections each ask for their own.

**Every save is checked against what is already there.** If someone else changed the
entry while you had it open, your save is refused, you are told who changed it and what
moved, and the screen reloads their change for you. Check your change is still needed, then
save it again. Fields the form had already saved before the conflict stay saved.

**Nothing is ever silently deleted.** Retiring a term or a code binding keeps it, with its
history, and stops it from being published. The catalogue's history for the entry — Who,
When, What, Why — is what every changelog note across all three parts builds.

**Computed figures are never inputs.** The preferred term's length, and anything else the
catalogue works out for you rather than asking you to type, has no field of its own anywhere
on this screen.

## If something goes wrong

Each guide has its own "If something goes wrong" section covering the refusals specific to
that part of the entry. These apply across all three parts:

**"You cannot edit this entry with your current sign-in."** See the note on multi-factor
authentication at the top of this page.

**"No catalogue entry was found for ... Check the identifier."** The entry could not be
opened at all — check the identifier in the address bar, or return to
[Administration → Catalogue](finding-entries.md) and find it from there.

**"... could not be loaded. Try again, or contact an administrator if the problem persists."**
The entry could not be opened, for a reason other than a wrong identifier. Try again; if it
keeps happening, contact an administrator.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. What you see may no longer be current. Sign in again and reopen the entry
before making further changes.

Whichever of these appears when the entry first opens, it is also announced for anyone
using a screen reader — the same wording, not just shown on screen.
