# Editing an entry's terms

Every catalogue entry has one **preferred term** (**RCPA Preferred**) and any number of
**synonyms** (**RCPA Synonyms**). This guide covers adding, editing, removing and reinstating
synonyms, and what to do when the catalogue tells you a term is already in use somewhere
else.

You need the Administrator role to change terms. Acknowledging a possible duplicate needs
only the Reviewer role.

Open an entry for editing at **Administration → Catalogue**, or go straight to
`/admin/catalogue/NPTC-000247/edit`. Terms are part of the one
[edit form](editing-an-entry.md), so they save with **Save** and share the form's changelog
note.

## What you see

- **RCPA Preferred** is the catalogue's own name for the test. Under the box you see its
  **length** in characters, updated as you type. The length is worked out by the catalogue.
  It is not stored and not typed, so it always matches the term it describes.
- **Synonym 1, Synonym 2 and so on** are the entry's active synonyms, one box each.
- **Add synonyms** is one box for new terms.
- **Retired synonyms** lists terms the entry used to hold, each with a **Reinstate** button.
  This list appears only when the entry has some.

A retired term is history, not something the entry currently publishes.

## Editing a term

Change the text in the box and choose **Save**. To change the preferred term, edit
**RCPA Preferred** the same way. The change is saved with your changelog note, together with
anything else you changed.

An edit that cleans to the same term is not a change. For example, adding a trailing space
sends nothing.

## Removing a synonym

Choose **Remove** beside the synonym. The box is replaced by a line saying the term **will be
removed when you save**. Nothing is sent yet. Choose **Keep** to change your mind.

When you choose **Save**, the term stops being published. The catalogue keeps it, and
records who removed it and why, in the entry's history. It then appears under **Retired
synonyms**.

**The preferred term cannot be removed.** Every entry must have a preferred term at all
times. To change what the entry is called, edit **RCPA Preferred**.

## Adding synonyms

Type one term in **Add synonyms**, or paste a whole cell from the old spreadsheet. Terms
separated by semicolons are split into one term each, so pasting `Zovirax;;Cyclir` adds
**Zovirax** and **Cyclir**: two terms, not three. The empty stretch between the doubled
semicolon is dropped, because a blank term is not a thing the catalogue can hold.

Before you save, the form tells you exactly what it will add: *"Save will add 2 terms:
“Zovirax”, “Cyclir”"*. Check the split matches what you meant.

You can add up to 100 terms at once. A larger paste is refused before it is sent, with a
count, so you can split it.

## Reinstating a retired term

Choose **Reinstate** beside a term under **Retired synonyms**, write a changelog note and
confirm. This saves at once, without **Save**.

The term is published again, and it is the same row as before. The entry's history still
reads as one continuous record (created, retired, reinstated) rather than a retirement
followed by an unrelated-looking new term. Reinstating is better than adding the term
afresh, because adding it again would start its history over.

If the term was re-added as a new synonym in the meantime, that synonym is already active,
so there is nothing to reinstate. Edit or remove the new synonym instead.

If the term became another live entry's preferred term while this one was retired,
reinstating is blocked the same way adding it would be. See
[When a term is already in use elsewhere](#when-a-term-is-already-in-use-elsewhere).

An earlier acknowledgement of a possible duplicate still applies after you reinstate.

If the reinstated term is also a synonym on another entry, the screen shows **After
reinstating a term** below the form, with the same **Check these** list and an
**Acknowledge** button for each duplicate.

## When a term is already in use elsewhere

The catalogue checks every term you save against every other live entry. It ignores
capitals, punctuation and unusual spaces when it compares, so `Adrenal Ab`, `adrenal ab`
and a version with a non-breaking space in it all count as the same term.

There are two outcomes, and they behave differently.

### The term is refused

If your term is another entry's **preferred term**, that term is refused. The summary names
the entry it clashes with, by its identifier. The box keeps what you typed, and your other
changes still save. Nothing was saved for that term.

Either choose a different term, or open that other entry and resolve it there first, for
example by removing the term over there if it belongs on your entry instead.

### The term is saved, with a note

If your term is already a **synonym** on another entry, the save succeeds and the term is
added. Two entries can legitimately share a synonym, so this is a note rather than a
refusal.

The summary then lists the term under **Check these**, naming the other entry. You have two
options:

- Change or remove the term, if the overlap was a mistake.
- Choose **Acknowledge** beside it and give a changelog note, if the overlap is intended.
  The catalogue records the decision and stops reporting that term on this entry from then
  on. This saves at once.

Acknowledging applies to this entry only. The other entry is untouched, and its own editors
still see the overlap until they acknowledge it themselves.

Acknowledgements cannot be withdrawn. If you acknowledge one by mistake, change or remove
the term instead.

### A preferred term over the maximum length, with a note

An administrator can set a maximum length for preferred terms. If you save a preferred term
longer than that maximum, the save still goes through. The summary lists it under **Check
these**, with its length and the maximum. For example: "The preferred term is 31 characters
long, which is over the maximum of 10. Consider shortening it."

There is nothing to acknowledge. Edit **RCPA Preferred** again if you want to shorten it. The
note describes the last save, so it is gone once you save again. If no maximum is set, which
is the default, you never see this note.

## If something goes wrong

**"This term is already in use on NPTC-000012, once case, spacing and punctuation are
ignored."** The refused case above, with the identifier of the entry it clashes with. Choose
a different term, or resolve it on that entry.

**"This entry already has an active designation for this term, once case, spacing and
punctuation are ignored."** The entry already holds this term. Check the synonym boxes. You
may be adding something that is already there under slightly different capitalisation.

**"Enter the synonym, or remove it."** A synonym box is empty. Type the term, or choose
**Remove**.

**Save stays unavailable, with a message under the changelog note.** The note is empty,
too short, or a single low-information word such as "update" or "fix". Write a sentence
saying what changed and why. Save becomes available as soon as the note passes.

**"This term could not be saved."** The term contains a character that has no single
correct repair, such as a zero-width space or a control character. This is usually picked up
by pasting from a formatted document. Retype the term rather than pasting it.

**"Someone else changed this entry while you had it open."** Another editor saved before
you did. Nothing of yours was saved from that point. The screen picks up their change for
you. Check yours is still needed, then save again.

**"No matching designation was found for the given term."** When editing or removing: the
term was removed or edited by someone else between the page loading and your save. When
reinstating: the term has no retired row on this entry to bring back. Reload the entry and
check the synonym boxes.

**"This term is already active on this entry, so there is nothing to reinstate."** The term
is already active. It was never retired, someone else already reinstated it, or it was
re-added as a new synonym after retiring. Reload the entry before trying again.

**"You cannot edit this entry with your current sign-in."** See
[Editing an entry](editing-an-entry.md#if-something-goes-wrong).
