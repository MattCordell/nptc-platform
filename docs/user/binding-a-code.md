# Binding a SNOMED CT code

Every catalogue entry can hold one **active code binding** — the SNOMED CT code the entry
publishes — plus any number of **retired** ones, kept for history. This guide covers
binding, retiring and replacing a code, and what to do when the terminology server cannot
answer.

You need the Administrator role to change bindings.

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

The **Code bindings** table lists every binding this entry has ever had — active and
retired — one row each: the code, its fully specified name, its AU preferred term, its
status, and, for a retired row, why it was retired and what replaced it if anything did.

Unlike the Terms table on this same screen, retired bindings stay listed here. A retired
code is still a code someone might be holding a reference to, and the entry that replaced
it is exactly what that person needs to find.

## You only ever type the code

Type a SNOMED CT code and the screen resolves its fully specified name, AU preferred term
and current status live against the terminology server, NCTS's own Ontoserver. You cannot
type either name yourself — there is no field for it. This is deliberate: a name that
could be typed is a name that could disagree with what SNOMED CT actually publishes for
that code, and this screen exists to make that impossible.

A code cannot be bound until it resolves. If you see **"This code must resolve against the
terminology server before it can be bound"**, the code has not yet resolved — check what
the screen shows underneath the code field:

- **A name and a status** — the code resolved. You can bind it.
- **"… was not found in the AU edition. Check the identifier."** — this code does not
  exist in the SNOMED CT Australian edition. Check you have the right code.
- **"The terminology server could not be reached…"** — the server could not be reached
  right now. This is not the same as the code not existing: wait a moment and try again,
  rather than assuming the code is wrong.

An **inactive** code can still be bound. The screen tells you it is inactive, but does not
stop you — the checks that would refuse an inactive code at bind time are a later stage of
the platform, not this screen. Look closely before binding one on purpose.

## Binding a code

Use **Bind a code**. Type the code, wait for it to resolve, give a changelog note, and
choose **Bind code**.

This form only appears while the entry has no active binding — an entry can have at most
one. If the entry already has one, retire or replace it first.

Every change needs a **changelog note**. It is the same field as the changelog note
elsewhere on this screen, and it becomes the published History text for the entry, so
write a sentence describing the change. Single words like "update" or "fix" are refused.

## Retiring a binding

Choose **Retire** on the active row, give a changelog note, and confirm.

The binding stops being active and the row updates to show it is retired, with your
reason. Nothing is deleted — the code and its history stay on the entry, and the entry can
now take a new binding.

Retiring on its own records no successor. If you are retiring this code **because**
another one replaces it, use Replace instead — Retire alone cannot record that link.

## Replacing a binding

Choose **Replace** on the active row. Type the successor's code, wait for it to resolve,
give a changelog note, and confirm.

This retires the current binding and binds the successor in one change, and records the
successor's code against the retired row — visible from that row in the table from then
on. One changelog note covers both steps, since replacing is one editorial decision, not
two.

## If something goes wrong

**"This code must resolve against the terminology server before it can be bound."** See
[You only ever type the code](#you-only-ever-type-the-code) above.

**"… was not found in the AU edition. Check the identifier."** The code does not exist in
the SNOMED CT Australian edition as far as the terminology server knows. Check the code.

**"The terminology server could not be reached…"** A live check could not be completed
right now. Every other part of this screen is unaffected — wait a moment and try again.

**"Enter a changelog note describing this change."** The note was empty. Write a sentence
saying what changed and why.

**"This entry already has an active code binding."** Someone else bound a code to this
entry while you had the screen open, or a second Bind attempt reached the server anyway.
Reload the entry and use Retire or Replace on the binding that is there now.

**"You cannot edit this entry with your current sign-in."** See the note on multi-factor
authentication at the top of this page.

**"... could not be refreshed just now, so what follows may be out of date."** The entry
loaded, but a later refresh was refused — usually a sign-in that has expired while the
screen was open. Sign in again and reopen the entry before making further changes.
