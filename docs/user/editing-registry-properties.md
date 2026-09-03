# Editing registry properties

Every catalogue entry can hold a value for each **registry property** an administrator has
defined — discipline, subgroup, specimen, and any others the registry has been extended
with. This guide covers recording, changing and retiring those values, and the entry's own
**accepts any specimen** setting.

You need the Administrator role to change registry properties.

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

The **Registry properties** table lists every property currently defined, one row each:
its label, whether it takes one value or several, what is currently recorded, and whether
it is still active or has been deprecated.

This table is generated from the registry's own definitions, not hand-built for each
property. If an administrator adds a new property definition elsewhere in the platform, it
appears here — with the right kind of input and the right value source — without anyone
changing this screen.

**A deprecated property that already has a value stays listed**, so nothing recorded
against it is lost, but there is no Edit action on that row: a deprecated property cannot
take a new value. A deprecated property with nothing ever recorded against it does not
appear at all.

## Editing a property's values

Choose **Edit** on any active row. What you see inside the dialog depends on the
property's own type:

- A short line of text, a longer block of text, or a number, as appropriate to what the
  property records.
- A **coded property** (discipline, subgroup, specimen, and others like them) shows a
  filter box and a list of codes to choose from — type to narrow the list, then pick one.
  The list always includes whatever code is already recorded, even if your filter does not
  match it, so an existing value is never dropped just because you were looking for
  something else.

A property that can take more than one value (**0..\*** or **1..\*** in the Cardinality
column) shows **Add another value** and a **Remove** button on each entry. A property that
takes at most one value shows a single input.

Give a changelog note and choose **Save**. Saving replaces this property's entire set of
values in one step — there is no way to add or remove a single value without resaving the
rest, which is also why one save covers everything shown in the dialog.

If a value you entered fails validation, the message appears against that value, not as a
generic refusal — you can see exactly which one to fix.

## Accepts any specimen (Any)

This is shown separately from the properties table, because it is not a property value at
all — it is a setting on the entry itself. An entry can either record specific specimen
codes through the **Specimen** property above, or accept any specimen, but not both at
once.

Choose **Edit** next to this setting, tick or untick the checkbox, give a changelog note,
and save. Turning this on while the entry already has specimen values recorded is refused
— clear those values first, through the Specimen property's own Edit dialog.

## If something goes wrong

**"Enter a changelog note describing this change."** The note was empty. Write a sentence
saying what changed and why.

**A message naming one of your values directly** (for example, "This value is too long.")
— that value failed validation. The message tells you what is wrong with it; the rest of
what you entered was not affected.

**"Someone else changed this entry while you had it open."** Another editor saved before
you did. Nothing of yours was saved. The screen picks up their change for you — check yours
is still needed, then save it again.

**"You cannot edit this entry with your current sign-in."** See the note on multi-factor
authentication at the top of this page.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. Sign in again and reopen the entry before making further changes.
