# Editing registry properties

Every catalogue entry can hold a value for each **registry property** an administrator has
defined — discipline, subgroup, specimen, and any others the registry has been extended
with. This guide covers recording, changing and retiring those values, including how to
record that a test accepts any specimen.

You need the Administrator role to change registry properties.

To see how a property itself is defined, such as its datatype, scope and binding, see
[Browsing the property registry](browsing-the-property-registry.md). To add a property,
change its label or flags, or retire it, see
[Managing registry properties](managing-registry-properties.md). This guide covers values
on an entry, not the property definitions themselves.

Open an entry for editing from **[Administration → Catalogue](finding-entries.md)** by
choosing its requesting term, or go straight to
`/admin/catalogue/NPTC-000247/edit` for the entry you want. Entries that have not been
published yet can be edited the same way as published ones.

## What you see

The edit form lists **Registry properties** below the SNOMED CT code and the synonyms, one
control for each active property, in the order the registry gives them. Each shows what is
currently recorded.

The controls are generated from the registry's own definitions, not hand-built for each
property. If an administrator adds a property definition elsewhere in the platform, it
appears here — with the right kind of input and the right value source — without anyone
changing this screen.

**A deprecated property that already has a value stays listed**, so nothing recorded
against it is lost. It is read-only: a deprecated property cannot take a new value. A
deprecated property with nothing ever recorded against it does not appear at all.

## Editing a property's values

Change the value in its control. What you see depends on the property's own type:

- A short line of text, a longer block of text, or a number, as appropriate to what the
  property records.
- A **coded property** (discipline, subgroup, specimen, and others like them) shows a
  filter box and a list of codes to choose from — type to narrow the list, then pick one.
  The list always includes whatever code is already recorded, even if your filter does not
  match it, so an existing value is never dropped just because you were looking for
  something else.

A property that can take more than one value (**0..\*** or **1..\*** in the registry) shows
**Add another value** and a **Remove** button on each entry. A property that takes at most
one value shows a single input.

Write a changelog note and choose **Save**. The form sends only the properties you changed.
Saving a property replaces its entire set of values in one step. There is no way to add or
remove a single value without resaving the rest of that property. Save stays unavailable —
with a message under the note explaining why — until the note is a real sentence describing
the change. Single words like "update" or "fix" are refused. Save is also unavailable until
you have changed something.

If a value you entered fails validation, the message appears against that value, not as a
generic refusal — you can see exactly which one to fix. The other changes in the same Save
still go through, and the summary under the form says which did not.

## Accepting any specimen

A test that accepts any specimen holds one **Specimen** value: the concept **Specimen**
(code `123038009`), on its own. Record it in the Specimen control, as you would any other
specimen. There is no separate setting for it. A note above the control reminds you which
value to pick: the picker names the concept **Specimen**, and an entry loaded from the
workbook shows the same value as **Any**.

The root cannot sit beside another specimen. If you try, the save of that property is
refused with a message against the value that conflicts: remove the root, or remove the
named specimens. An entry with no specimen value at all is one nobody has filled in yet,
which is a different fact from accepting any specimen.

## If something goes wrong

**Save stays unavailable.** Either nothing has changed yet, or the changelog note is empty,
too short, or a single low-information word such as "update" or "fix". The screen tells
you which, before you try to save. Write a sentence saying what changed and why; Save
becomes available as soon as the note passes.

**A message naming one of your values directly** (for example, "This value is too long.")
— that value failed validation. The message tells you what is wrong with it. The summary
under the form lists it as not saved. Your other changes were saved, and your value stays in
the control so you can correct it and save again.

**"Someone else changed this entry while you had it open."** Another editor saved before
you did. The save stopped at that point. Fields saved before it stay saved, and the summary
lists the ones that were not sent. The screen picks up their change for you — check yours is
still needed, then save again.

**"You cannot edit this entry with your current sign-in."** See
[Editing an entry](editing-an-entry.md#if-something-goes-wrong).

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. Sign in again and reopen the entry before making further changes.
