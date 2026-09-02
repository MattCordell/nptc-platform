# Editing an entry's terms

Every catalogue entry has one **preferred term** and any number of **synonyms**. This
guide covers adding, editing and retiring them, and what to do when the catalogue tells
you a term is already in use somewhere else.

You need the Administrator role to change terms. Acknowledging a possible duplicate needs
only the Reviewer role.

Open an entry for editing at **Administration → Catalogue → Edit**, or go straight to
`/admin/catalogue/NPTC-000247/edit` for the entry you want. Entries that have not been
published yet can be edited the same way as published ones.

> **Multi-factor authentication is not yet prompted for automatically.** Changing the
> catalogue requires an administrator account that has completed the second sign-in step.
> If you have not, this screen refuses to load and tells you to sign in again. Until the
> automatic prompt lands, sign out and sign in again, completing the second step, then
> return here. See the follow-up issue linked from
> [ADR-0021](../adr/0021-browser-side-pkce-login.md).

## What you see

The **Terms** table lists everything the entry publishes, one row per term:

- The **preferred term** first. This is the catalogue's own name for the test.
- Every **synonym** after it.

Retired terms are not listed. They are kept, with their history, but the table shows what
the entry publishes today.

**Length** is the character count of the preferred term. It is worked out by the
catalogue, not stored and not typed, so it always matches the term it describes. There is
no way to edit it, and that is deliberate: a length that could be edited separately from
its term is a length that can be wrong.

## Adding synonyms

Use **Add synonyms**.

You can type a single term, or paste a whole cell from the old spreadsheet. Terms
separated by semicolons are split into one row each, so pasting
`Zovirax;;Cyclir` adds **Zovirax** and **Cyclir** — two terms, not three. The empty
stretch between the doubled semicolon is dropped, because a blank term is not a thing the
catalogue can hold.

You can add up to 100 terms at once. A larger paste is refused before it is sent, with a
count, so you can split it.

Before you save, the screen tells you exactly what it will create — *"This will add 2
terms: “Zovirax”, “Cyclir”"* — so you can check the split matches what you meant.

Every change needs a **changelog note**. It becomes the published History text for the
entry, so write a sentence describing the change. Single words like "update" or "fix" are
refused.

## Editing a term

Choose **Edit** on any active row. Change the term, add a changelog note, and choose
**Save term**.

Editing the preferred term works the same way as editing a synonym, even though the
catalogue stores the two differently behind the scenes.

If someone else changes the entry while you have it open, your save is refused and you are
told who changed it, when, and which values moved. Nothing of yours is saved. The screen
starts reloading their change for you, so check yours is still needed and save it again.

## Retiring a synonym

Choose **Retire** on the row, give a changelog note, and confirm.

The term stops being published and disappears from the Terms table. Nothing is deleted:
the catalogue keeps the term and records who retired it and why, in the entry's history.

**The preferred term cannot be retired**, so no Retire button appears on that row. Every
entry must have a preferred term at all times. To change what the entry is called, edit
the preferred term instead.

## When a term is already in use elsewhere

The catalogue checks every term you save against every other live entry. It ignores
capitals, punctuation and unusual spaces when it compares, so `Adrenal Ab`, `adrenal ab`
and a version with a non-breaking space in it all count as the same term.

There are two outcomes, and they behave differently.

### The save is blocked

If your term is another entry's **preferred term**, the save is refused. The screen names
the entry it clashes with and links to it. Nothing is saved.

Either choose a different term, or open that other entry and resolve it there first — for
example by retiring the term over there, if it belongs on your entry instead.

### The save goes through, with a note

If your term is already a **synonym** on another entry, the save succeeds and the term is
added. Two entries can legitimately share a synonym, so this is a note rather than a
refusal.

The term then appears under **Possible duplicates**, naming the other entry. You have two
options:

- Change or retire the term, if the overlap was a mistake.
- Choose **Acknowledge** and give a changelog note, if the overlap is intended. The
  catalogue records the decision and stops reporting that term on this entry from then on.

Acknowledging applies to this entry only. The other entry is untouched, and its own
editors still see the overlap until they acknowledge it themselves.

Acknowledgements cannot be withdrawn. If you acknowledge one by mistake, change or retire
the term instead.

## If something goes wrong

**"This term is already in use on another entry."** The blocked case above. Nothing was
saved. Choose a different term, or resolve it on the entry named in the message.

**"This entry already has an active designation for this term, once case, spacing and
punctuation are ignored."** The entry already holds this term. Check the Terms table — you
may be adding something that is already there under slightly different capitalisation.

**"A changelog note is required and must describe the change."** The note was empty, too
short, or a single low-information word. Write a sentence saying what changed and why.

**"This term could not be saved."** The term contains a character that has no single
correct repair — a zero-width space, or a control character — usually picked up by pasting
from a formatted document. Retype the term rather than pasting it.

**"Someone else changed this entry while you had it open."** Another editor saved before
you did. Nothing of yours was saved. The screen picks up their change for you - check yours
is still needed, then save it again.

**"No active designation was found for the given term."** The term was retired or edited
by someone else between the page loading and your save. Reload the entry.

**"You cannot edit this entry with your current sign-in."** See the note on multi-factor
authentication at the top of this page.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused - usually a sign-in that has expired while the
screen was open. The terms you can see may no longer be current. Sign in again and reopen
the entry before making further changes.
