# Binding a SNOMED CT code

Every catalogue entry can hold one **active code** (its SNOMED CT binding), plus any number
of **retired** ones, kept for history. This guide covers finding a code, binding it,
replacing it, retiring it, and what to do when the terminology server cannot answer.

You need the Administrator role to change a code.

Open an entry for editing at **Administration → Catalogue**, or go straight to
`/admin/catalogue/NPTC-000247/edit`. The **SNOMED CT code** section is part of the one
[edit form](editing-an-entry.md), so it saves with **Save** and shares the form's changelog
note.

## What you see

The section shows the entry's active code with its fully specified name and its AU
preferred term. If the entry has no code, it says so.

Retired codes stay listed under **Retired SNOMED CT codes**, with the reason and the code
that replaced each one, if any did. A retired code is still a code someone might hold a
reference to, and the code that replaced it is what that person needs to find.

## Finding a code

Type in the search box. It is called **SNOMED CT code** when the entry has no code, and
**Replacement SNOMED CT code** when it has one. You can type:

- **A term**, such as `microscopy`. The terminology server (NCTS's Ontoserver) matches it,
  and the matching concepts appear as a list of codes with their AU preferred terms.
- **A whole code.** The matching concept appears if it is one the catalogue accepts.

**Only active procedures are offered.** The search covers the concepts under Procedure in
SNOMED CT, and not Procedure itself. A code from anywhere else, such as a disorder or a
body structure, is never listed. If you type one, the box says **"No procedure matches.
Only concepts under Procedure can be bound."** This is deliberate. It is the only check
at save time that the code is a procedure.

A long list says **"Showing 20 of 140."** Type more to narrow it.

Use the arrow keys to move through the list. The first result is highlighted, so **Enter**
chooses it.

## You only ever pick the code

Choose a result and the form reads its fully specified name, AU preferred term and status
from the terminology server. You cannot type either name. A name that could be typed is a
name that could disagree with what SNOMED CT publishes for that code, and this screen exists
to make that impossible.

Choose **Clear the chosen code** to change your mind.

If the server returns no name for the code, the form says so and does not let you save it.
If you choose **Save** while a code is still being checked, you see **"Wait for the code to
finish checking before saving."**

## Binding a code

When the entry has no code, choose the code, write the changelog note and choose **Save**.
The form says **"Saving binds this code to the entry."**

## Replacing a code

When the entry has a code, choose a replacement and **Save**. The form says **"Saving
retires 391483001 and binds this code in its place."**

One request retires the old code, binds the new one and records the new code against the
retired row. One changelog note covers both steps, since replacing is one editorial
decision. If someone else changes the entry first, none of the steps run.

## Retiring a code with no replacement

Choose **Retire without a replacement** beside the active code, write a changelog note and
confirm. This saves at once, without **Save**.

The code stops being active and stays listed as retired, with your reason. The entry now has
no code and can take a new one. Retiring on its own records no successor. If another code
takes its place, use a replacement instead, because only a replacement records that link.

## When the terminology server cannot answer

The box says so, using the server's own sentence, followed by **"You can still change the
rest of this entry."** The rest of the form works. You can save your other changes now and
come back for the code. This is not the same as a code that does not exist, so wait a moment
and try again before assuming the code is wrong.

## If something goes wrong

**"No procedure matches. Only concepts under Procedure can be bound."** Nothing the server
holds matches what you typed, or the match is not a procedure. Check the term or the code.

**"The terminology server did not return a name for ... It cannot be bound until it does."**
The server knows the code but returned no name for it. Try another code, or try again later.

**"This code is already actively bound to another catalogue entry."** A code can be active on one
entry only. Find that entry and retire the code there first. The save marks the code field,
and your other changes still save.

**"This entry already has an active code binding."** Someone else bound a code while you had
the screen open. Reload the entry and replace the code that is there now.

**"Someone else changed this entry while you had it open."** Another editor saved first.
Nothing of yours was saved from that point. The screen reloads their change. Check yours is
still needed, then save again.

**Save stays unavailable, with a message under the changelog note.** The note is empty, too
short, or a single low-information word such as "update" or "fix". Write a sentence saying
what changed and why.

**"You cannot edit this entry with your current sign-in."** See
[Editing an entry](editing-an-entry.md#if-something-goes-wrong).
