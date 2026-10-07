# Searching the audit log

Every change this platform makes - to a catalogue entry, a code binding, a term, a registry
property, a user's account - is recorded in an audit log that nothing, not even an
Administrator, can edit or delete. This page explains how to search the log on the **Audit
log** screen, and what an export contains.

Open the screen from the Admin area, at `/admin/audit`. Only Administrators can use it.

## Searching

The screen shows the newest events first, 50 at a time. Use the form above the results to
narrow them. Every filter is optional. Filters you fill in are combined, so an event must
match all of them to appear.

- **Actor** - who made the change. Enter the person's user id, which looks like
  `3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8`. You rarely need to type it: in the results, select
  an actor's name and the screen filters by that person for you.
- **Entity type** - what kind of thing changed, for example `catalogue_entry`.
- **Entity id** - which one it was. You need an entity type as well.
- **Action** - the kind of change, for example `catalogue_entry.updated`. Type the name
  exactly.
- **From** and **To** - the first and last day to include. Both days count.

Select **Apply filters** to search. Each filter you applied appears as a chip under the form.
Select a chip to remove that filter, or select **Clear all filters** to start again. The web
address holds your filters, so you can copy it to share the same view.

### Days and times

The screen uses Australian Eastern Standard Time (UTC+10) for the dates you enter and for the
times it shows. It does not change for daylight saving. The **When (UTC+10)** column shows each
event in the same time, so what you filter on matches what you read.

### Reading the results

Each row shows when the change happened, who made it, the action, the entity it affected, and
the reason given for it. A reason reads "None recorded" when the person gave none.

- **System** in the Actor column means the platform itself made the change, not a person.
- **Closed account** means the person's account has since been closed. See the next section.

Use **Next page** and **Previous page** under the table to move through the results. The
screen does not show a page number or a total, because the log keeps growing while you read
it. A change recorded while you page through cannot appear part-way through a page you have
already seen.

**Previous page** follows only the buttons you pressed on the screen. If you use your browser's
Back and Forward buttons to change page, the screen shows the page you asked for but no longer
offers **Previous page**. Use **Next page** to go on, or apply your filters again to start from
the newest events.

## Attribution to a closed account

If the person who made a change has since had their account closed, their name is no longer
shown - closing an account removes the name from it everywhere, on purpose (this platform
never simply deletes an account; it keeps the record but takes the name off it). The Actor
column reads **Closed account** instead. You can still select it to find every change by that
account, because the screen filters by the account's internal reference. The change itself,
and everything else about it, is unaffected.

## Exporting

Select **Export as NDJSON** at the top of the screen to download the events that match the
filters you have applied. The file is named `audit-events.ndjson`.

An export holds everything your applied filters would return, not just the current page. It
lists the oldest event first. It is one line of detail per change. Each line carries all the
details of the event, including the values before and after the change, which the results
table does not show. Each line also carries two technical fields (a "hash") that a technically
able reader with direct access to the platform's database can use to confirm one exported line
still matches what the platform itself has recorded for that change. These fields are not a
substitute for that database access: they let someone check an exported line against the
platform's own record of it, not recompute the check from the exported file alone.

The export uses the filters you have applied, not edits you have typed but not yet applied.
Select **Apply filters** first if you want the export to include them.

An export is not itself recorded as a change in the log: reading and exporting are the only
two things this part of the platform can do, and neither writes anything.

## If something goes wrong

Searching and exporting need the same permission, and both need the extra sign-in step
(multi-factor authentication) described in
[Signing in](signing-in.md#multi-factor-authentication) - see [Roles](roles.md) for who
holds it.

- **"You do not have permission to do this."** Your account cannot read the audit log. The
  screen shows no results. Ask an Administrator.
- **A request to complete multi-factor authentication.** The screen starts the extra sign-in
  step for you and then shows the results. If you started an export, select **Export as
  NDJSON** again after you finish the step. The screen does not repeat the export for you.
- **"Some filters need fixing."** A filter you entered is not valid, for example an actor that
  is not a user id, an entity id without an entity type, or a **To** day before the **From**
  day. The message names each problem and links to the field. The screen sends nothing until
  you fix them.
- **"The audit log could not be loaded."** Try again in a moment. If the problem continues,
  contact an administrator.
