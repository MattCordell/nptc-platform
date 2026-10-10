# Submitting a new test

Use this screen to propose a test that is not yet in the catalogue. A reviewer checks every
proposal before anything is added.

You need to be signed in. Provisional users, Members, Reviewers and Administrators can
submit. If your account cannot, the form shows the server's refusal when you submit, and
nothing is sent.

Open the form at `/submissions/new`. The page title is **Submit a new test**.

## What you fill in

Fields marked **(required)** must be filled in before you can submit.

| Field | What to enter |
| --- | --- |
| **Test name (required)** | The name the test is requested by. |
| **Other names** | Other names the test is known by. Choose **Add another name** for more rows, or paste a list separated by semicolons to fill one row per name. Empty rows are ignored. |
| **SNOMED CT code** | Optional. Search by term or by code, then choose a result. See [Choosing a code](#choosing-a-code). |
| **Properties** | One field for each property an administrator has made part of a proposal. A property marked **(required)** needs a value. This list changes when an administrator changes a property, so it can differ from what this guide shows. |
| **Reference link (required)** | A web page that supports the test, such as a guideline or a published method. |
| **Notes** | Anything a reviewer should know. |
| **Organisation** | Filled in from your profile. Change it if the test comes from a different organisation. |
| **Submitted by** | Your name. You cannot change it. |

The form never asks for values the platform works out itself, such as a name's length.

## Choosing a code

Type a term or a SNOMED CT code in **SNOMED CT code**. Only procedures are offered. Choose
a result and the form shows its fully specified name and AU preferred term. You cannot type
either name, because the terminology server supplies them.

Choose **Clear the code** to change your mind. A code you do not clear is sent with the
test, so wait until the form has finished checking it.

If the terminology server cannot be reached, the code field says so. You can still send
the rest of the form: choose **Clear the code** and submit without one.

## The duplicate check

When you choose **Submit test**, the platform first looks for catalogue entries and open
submissions that look like yours. It compares the name, the other names and the code.

- **Nothing matches.** The test is submitted.
- **Something matches.** The page shows **This test may already exist** and lists the
  records. A catalogue entry is a link, so you can open it and compare. An open submission
  is listed without a link, and without the name of the person who made it. Nothing has been
  submitted yet.

To go on, tick the box that says your test is different, then choose **Submit this test**.
The platform records that you confirmed. To change your entry instead, choose **Change my
entry**. What you typed is still there.

## When something goes wrong

The page shows what to fix beside the field it concerns, and lists every problem at the
top. Choose a problem in the list to move to its field. Nothing you typed is lost.

| What you see | What it means | What to do |
| --- | --- | --- |
| A message under **Reference link** | The page could not be used. The message gives the reason, such as a status code or a host name that does not exist. A message that the platform cannot reach the internet means the platform could not check the link, and the link may be fine. | Fix the link and submit again. For the internet message, try again later or contact an administrator. |
| A message under **SNOMED CT code** | The terminology server rules the code out, or could not be reached. | Choose another code, or clear the code and submit without one. |
| A message under **Test name** or **Other names** | A name is empty after cleaning, or holds an invisible character. | Retype the name. |
| A message under a property | The value is not allowed for that property. | Correct the value. |
| **You have reached the limit** | You have used up your submission quota. | See [Submission limits](#submission-limits). |
| **You need to accept the current terms of use** | The terms changed since you last accepted them. | Accept the terms when the page asks. The form returns with your answers in it. Submit again. |
| **You do not have permission to do this** | Your account cannot submit tests. | Contact an administrator. |

## Submission limits

Your account has a limit on submissions. New tests and amendments share one count.

- **A limit per hour.** The message says when you can submit again. It counts down from the
  time the server gave, and rounds up.
- **A limit in total.** The message says that waiting will not lift the limit, because it
  does not. Contact an administrator if you need to submit more.
- **An earlier submission still being processed.** Try again in a few seconds.

A refused submission is not saved.

## After you submit

The page shows **Your test was submitted** and the test's name. A reviewer will look at it.
Choose **Submit another test** for a clean form, or **Back to the catalogue**.

There is no page yet for following a submission through review.
