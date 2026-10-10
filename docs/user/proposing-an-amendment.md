# Proposing a change to an entry

Use this screen to suggest an addition to a test that is already in the catalogue: most
often another name for it, or a SNOMED CT code. A reviewer checks every proposal before the
entry changes. Nothing changes on the entry when you send a proposal.

You need to be signed in. Provisional users, Members, Reviewers and Administrators can
propose a change. The **Propose a change** link does not check your role. If your account
cannot propose, the form shows the server's refusal when you send it, and nothing is saved.

## Open the form

1. Open the entry in the catalogue.
2. Choose **Propose a change**, beside the entry's name.

The link shows only when you are signed in and the entry is active. A draft, deprecated or
withdrawn entry cannot be amended, so it has no link. A visitor who is not signed in sees no
link, and a page that is still checking your sign-in shows none yet.

The form opens at `/submissions/new?entry=` followed by the entry's key, for example
`/submissions/new?entry=NPTC-000006`. If you open that address while signed out, you are asked
to sign in and then return to the form. To propose a new test instead, use
[Submitting a new test](submitting-a-new-test.md).

## What you see

**The entry now** shows the entry's name, its other names and its SNOMED CT code. You cannot
change them here. An amendment adds to the entry. It does not rename it or edit its other
details.

## What you fill in

You must give at least one new other name or a SNOMED CT code. Everything else is optional.

| Field | What to enter |
| --- | --- |
| **New other names** | Other names to add to the entry. Choose **Add another name** for more rows, or paste a list separated by semicolons to get one row per name. Empty rows are ignored. |
| **SNOMED CT code** | A code to propose for the entry. Search by term or by code, then choose a result. The form shows its fully specified name and AU preferred term, which the terminology server supplies. See [Choosing a code](submitting-a-new-test.md#choosing-a-code). |
| **Reference link** | A web page that supports the change. If you give one, the platform checks that the page answers. |
| **Notes** | Anything a reviewer should know. |
| **Organisation** | Filled in from your profile. Change it if the change comes from a different organisation. |
| **Proposed by** | Your name. You cannot change it. |

If you send the form with no name and no code, it asks you to add one, and sends nothing.

## When something goes wrong

The page shows what to fix beside the field it concerns, and lists every problem at the top.
Nothing you typed is lost.

| What you see | What it means | What to do |
| --- | --- | --- |
| **This entry can no longer be amended.** followed by a reason | The entry stopped being active after you opened the form. The reason names its status. | Go back to the entry. Contact an administrator if you think the status is wrong. |
| A sentence saying there is nothing to propose | The entry already has every name and code you gave. | Add a name or a code the entry does not have. |
| A message under **New other names** | A name is empty after cleaning, or holds an invisible character. | Retype the name. |
| A message under **SNOMED CT code** | The terminology server rules the code out, could not be reached, or gave an answer the platform could not use. | Choose another code, or clear the code and propose names alone. |
| A message under **Reference link** | The page could not be used. The message gives the reason. | Fix the link, or leave it blank. |
| **You have reached the limit** | You have used up your submission quota. New tests and amendments share one count. | See [Submission limits](submitting-a-new-test.md#submission-limits). |
| **You need to accept the current terms of use** | The terms changed since you last accepted them. | Accept the terms when the page asks. The form returns with your answers in it. Send it again. |
| **You do not have permission to do this** | Your account cannot propose changes. | Contact an administrator. |
| **We couldn't find that page** | No public entry has that key. | Check the address, or search the catalogue. |
| **This entry could not be loaded** | The platform could not read the entry. | Choose **Try again**. |

## After you send it

The page shows **Your change was proposed**. A reviewer will look at it. Choose **Back to the
entry** or **Back to the catalogue**.

There is no page yet for following a proposal through review, and an approved change is not
applied to the entry on this screen.
