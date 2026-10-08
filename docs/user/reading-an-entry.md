# Reading an entry

Each published test in the National Pathology Test Catalogue has its own page at
`/catalogue/{businessKey}`, for example `/catalogue/NPTC-000247`. You do not need an
account. To reach a page, choose a test's name in [the catalogue search
results](searching-the-catalogue.md).

## The top of the page

- **The breadcrumb** shows where you are: Home, Catalogue, then the test. Choose **Home** or
  **Catalogue** to go back up.
- **The heading** is the test's requesting term.
- **The code** sits below the heading, in a grey box. It is the SNOMED CT code the test is
  bound to now. It shows every digit and never shortens. **No SNOMED CT code** means the test
  has none.
- **The status** is written in words, such as **Active**.
- **Open finding** appears next to the status when an automated check has flagged the test.
  It says only that a finding exists. See [What the finding indicator
  means](viewing-the-finding-indicator.md).

## The main column

### Terms

The **Terms** table lists every name the test goes by. Each row gives the term and its type.
The rows come in this order:

1. **RCPA Preferred** is the test's requesting term, the same as the page heading.
2. **RCPA Synonym** rows follow, one for each synonym.
3. **SNOMED CT FSN** is the fully specified name of the code the test is bound to now. It
   appears exactly as SNOMED CT gives it, with its bracketed tag, such as "(procedure)".
4. **SNOMED CT Preferred** is the Australian preferred term for that code. The row is
   absent if none is recorded.

A test with no active SNOMED CT code shows the RCPA rows only.

### Retired SNOMED CT codes

The **Retired SNOMED CT codes** table appears only when the test has been bound to a code
that is now retired. It stays listed so that you can recognise a code you already hold and
see what replaced it.

| Column | What it shows |
|---|---|
| **Code** | The SNOMED CT code, as an exact string of digits. |
| **Fully specified name** | The name SNOMED CT gives the code, exactly as recorded. |
| **Retirement** | The reason it was retired and, if there is one, **Replaced by** the code that took its place. |

## The details column

Beside the main column, or below it on a narrow screen. **Details** lists, in this order:

- **Identifier** is the test's NPTC identifier.
- **Status** repeats the status.
- **Disciplines** lists each discipline recorded for the test. **None recorded** means none
  is.
- **Every other property** recorded for the test, such as **Specimen**, **Subgroup** and
  **Usage guidance**. A value taken from a code list shows its term. When the code is a
  SNOMED CT code, the code follows in a grey box, except for **Specimen**, which shows its
  term alone. A property with several values lists them one under another. Where a reason
  was recorded for a value, it appears below the value as **Justification**. A property
  marked **Deprecated** is no longer used for new entries, but its recorded value is kept.
- **Last updated** is the date the entry last changed.

**Specimen** drops a closing word "specimen", so "Serum specimen" reads **Serum**. A test
that accepts any specimen lists the root concept, which reads **Specimen**. There is no
separate **Any specimen** row.

The page does not show the length of the requesting term.

### Recent changes

**Recent changes** lists the latest five changes, newest first. Each shows the date, what
happened, such as "Designation created", the fields it touched, and the note the editor
wrote, if any. The list leaves out internal identifiers, which mean nothing to a reader.

The name of the person who made a change appears only when you are signed in. Choose **View
full history** below the list to see every change, fifty at a time. See
[Viewing an entry's change history](viewing-entry-history.md). The link is absent when no
change is recorded or the recent changes could not be loaded.

## If the test is not found

The page says **We couldn't find that page** when:

- no test has that identifier, or
- the test is not published, or
- the identifier is not written in the form `NPTC-000247`.

The page cannot tell these apart, and does not say which applies. Choose **Search the
catalogue** to find the test by name or code.

## If something goes wrong

- If the test cannot be loaded, the page says so and offers **Try again**.
- If the page has a test on screen but cannot refresh it, it keeps showing it and warns you
  it may be out of date.
- If **Recent changes** cannot be loaded, that box says so. The rest of the page still works.

## Using it with a keyboard or screen reader

Press Tab to move through the page: the breadcrumb links, then any links and controls in the
main column. A visible ring shows where you are. The page has one main heading, and each
block below it has its own heading, so a screen reader can jump between **Terms**, **Retired
SNOMED CT codes** (when it appears), **Details** and **Recent changes**. Status is always written in
words as well as shown in colour. A screen reader announces it when the entry cannot be
loaded or refreshed, and when **Recent changes** cannot be loaded.
