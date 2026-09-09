# Searching the audit log

Every change this platform makes - to a catalogue entry, a code binding, a term, a registry
property, a user's account - is recorded in an audit log that nothing, not even an
Administrator, can edit or delete. This page explains what you can search for and what an
export contains.

> **This page describes API behaviour, not a screen yet.** There is no audit log screen in
> this platform's admin interface yet - searching and exporting is available today to a
> developer or an integrator reading the API directly. The screen itself is a separate,
> follow-up piece of work. See issue #286.

## What you can search for

Four filters, usable on their own or together:

- **Actor** - who made the change, by account.
- **Entity** - what was changed (a catalogue entry, a code binding, a term, and so on), and
  optionally which specific one.
- **Action** - the kind of change, e.g. "code binding created" or "entry status changed."
- **Date range** - when the change happened.

Results come back most recent first, a page at a time. Search results never go stale mid-way
through: if a new change is recorded while you are paging through results, it cannot appear
part-way through a page you have already seen.

## Attribution to a closed account

If the person who made a change has since had their account closed, their name is no longer
shown - closing an account removes the name from it everywhere, on purpose (this platform
never simply deletes an account; it keeps the record but takes the name off it). What you
see instead is a marker showing the account is closed, plus the same internal reference the
platform itself uses to identify that account. The change itself, and everything else about
it, is unaffected.

## What an export contains

An export downloads everything your current search would return, as one line of detail per
change - not just the current page, the whole result set. Each line also carries two
technical fields (a "hash") that let you or anyone else independently confirm the exported
lines have not been altered since they were recorded, without needing to trust the platform's
word for it.

An export is not itself recorded as a change in the log: reading and exporting are the only
two things this part of the platform can do, and neither writes anything.

## If something goes wrong

Searching and exporting need the same permission, and both need the extra sign-in step
(multi-factor authentication) described in
[Signing in](signing-in.md#multi-factor-authentication) - see [Roles](roles.md) for who
holds it. A search or export request that arrives without completing that step is refused
in the same way any other administrative action is.
