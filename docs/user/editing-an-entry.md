# Editing an entry

The catalogue entry editing screen is one form. It covers the entry's **RCPA Preferred**
term, its **SNOMED CT code**, its **RCPA Synonyms** and every registry property. You change
what is wrong, write one changelog note and choose **Save**.

You need the Administrator role to change an entry. Acknowledging a possible duplicate term
is the one exception. That needs only the Reviewer role.

Open an entry for editing from **[Administration → Catalogue](finding-entries.md)**, or go
straight to `/admin/catalogue/NPTC-000247/edit` for the entry you want. Entries that have not
been published yet can be edited the same way as published ones.

## What the form holds

- **Identifier.** For example `NPTC-000006`. It is shown as text. The platform creates it
  and it never changes.
- **RCPA Preferred.** The catalogue's own name for the test. Under the box you see its
  length in characters. The length updates as you type. It is counted the way the catalogue
  counts it, after the spacing is cleaned, so a trailing non-breaking space does not add to
  it. After you save, the form also shows the length the server counted.
- **SNOMED CT code.** The entry's active code, and a search box to bind or replace it. See
  [Binding a SNOMED CT code](binding-a-code.md).
- **RCPA Synonyms.** One box for each active synonym, a **Remove** button on each, and a box
  to add more. See [Editing an entry's terms](editing-designations.md).
- **Registry properties.** One control for every registry property, in the registry's own
  order. See [Editing registry properties](editing-registry-properties.md).
- **Changelog note.** One note for the whole save.

## How the form saves

Save sends only the fields you changed, one at a time, in this order: the preferred term,
the code, the synonyms, then each property. All of them share your changelog note.

- **A field the server refuses.** The field is marked with the reason, and it keeps what you
  typed. The fields after it are still saved.
- **A stale entry, a sign-in problem or a server failure.** The save stops, because the
  fields after it would fail for the same reason. Fields not yet sent stay on screen.
- **Afterwards.** A summary lists the fields that saved, the fields that did not and why.
  Screen readers announce it too. If something did not save, focus moves to the summary.

To retry, fix the marked fields and choose **Save** again. Only fields that are not yet
saved are sent.

## Three changes save on their own

Three changes are one decision about one row, so each opens its own dialog, asks for its own
changelog note and saves at once. They do not wait for **Save**.

- **Reinstate** a retired synonym.
- **Acknowledge** a possible duplicate term.
- **Retire without a replacement** the entry's active code.

## What is common to every change

**Every change needs a changelog note.** It becomes the published History text for the
entry, so write a sentence describing the change. Single words like "update" or "fix" are
refused, and the save button stays unavailable, telling you why next to the note itself,
until you write one that passes.

**Every save is checked against what is already there.** If someone else changed the
entry while you had it open, your save is refused, you are told who changed it and what
moved, and the screen reloads their change for you. Check your change is still needed, then
save it again. Fields the form had already saved before the conflict stay saved.

**Nothing is ever silently deleted.** Removing a synonym or retiring a code keeps it, with
its history, and stops it from being published. The catalogue's history for the entry (who,
when, what, why) is built from every changelog note.

**Computed figures are never inputs.** The preferred term's length, and anything else the
catalogue works out for you rather than asking you to type, has no field of its own on this
screen.

## If something goes wrong

Each guide has its own "If something goes wrong" section for the refusals specific to that
part of the entry. These apply across the whole screen:

**"You cannot edit this entry with your current sign-in."** Your sign-in does not allow
editing. Ask an administrator for the Administrator role, or sign in again with
multi-factor authentication if you were asked to.

**"No catalogue entry was found for ... Check the identifier."** The entry could not be
opened at all. Check the identifier in the address bar, or return to
[Administration → Catalogue](finding-entries.md) and find it from there.

**"... could not be loaded. Try again, or contact an administrator if the problem persists."**
The entry could not be opened, for a reason other than a wrong identifier. Try again. If it
keeps happening, contact an administrator.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused. This is usually a sign-in that has expired while
the screen was open. What you see may no longer be current. Sign in again and reopen the
entry before making further changes.

Whichever of these appears when the entry first opens, it is also announced for anyone
using a screen reader, with the same wording.
