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

The **Terms** table lists the test's synonyms and its preferred terms in other languages.
Each row gives the term, its type, its language and its status. The test's own requesting
term is the page heading, so it is not repeated here.

### SNOMED CT codes

The **SNOMED CT codes** table lists every code the test has been bound to.

| Column | What it shows |
|---|---|
| **Code** | The SNOMED CT code, as an exact string of digits. |
| **Fully specified name** | The name SNOMED CT gives the code, exactly as recorded. It keeps its bracketed tag, such as "(procedure)". |
| **AU preferred term** | The Australian preferred term, if one is recorded. |
| **Status** | **Active** or **Retired**, in words. |
| **Retirement** | For a retired code, the reason it was retired and, if there is one, **Replaced by** the code that took its place. |

The active code comes first. Retired codes follow. They stay listed so that you can
recognise a code you already hold and see what replaced it.

### Properties

**Properties** lists every property recorded for the test, such as its disciplines or usage
guidance. A property with several values lists them one under another. Where a reason was
recorded for a value, it appears below the value as **Justification**. A property marked
**Deprecated** is no longer used for new entries, but its recorded value is kept.

## The details column

Beside the main column, or below it on a narrow screen:

- **Identifier** is the test's NPTC identifier.
- **Status** repeats the status.
- **Term length** is the number of characters in the requesting term.
- **Disciplines** lists each discipline recorded for the test. **None recorded** means none
  is.
- **Any specimen** is **Yes** when the test accepts any specimen, and **No** when it does
  not. **No** does not name the specimens. Look in **Properties** for those.
- **Last updated** is the date the entry last changed.

### Recent changes

**Recent changes** lists the latest five changes, newest first. Each shows the date, what
changed, and the note the editor wrote, if any. A change made by the system, not an editor,
shows only what changed.

The name of the person who made a change appears only when you are signed in. This page does
not link to the full history yet.

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
block below it has its own heading, so a screen reader can jump between **Terms**, **SNOMED
CT codes**, **Properties**, **Details** and **Recent changes**. Status is always written in
words as well as shown in colour.
