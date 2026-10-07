# Design system

Issue #323. The maintainer's design notes, carried close to verbatim, plus a short section
reconciling them against the platform's real lifecycle status values. This issue lands the
tokens, fonts and three shared components (`Button`'s hover states, `DataTable`'s header/
row-hover/`align`, and the new `StatusBadge`) that consume this vocabulary — see
[components.md](components.md) for the component baseline itself.

**This document describes the target design, not the current state of every screen.** The
app shell (issue #426: header with a user menu, and footer) and the landing page
(issue #428: a flat serif hero at `text-4xl` with a search box, then three cards; it
writes its own `h1` because `PageHeader` has one fixed title size) are implemented, as is
the sign-in and registration treatment in the Keycloak login theme (issue #432). The search
input, filter bar and pagination exist as shared components (issue #438). The admin
catalogue list uses the search input, the filter bar's active-filter chips and pagination,
with a checkbox facet panel in place of pill toggles; it also shows status as a
`StatusBadge`, codes in monospace and dates in tabular figures. The admin edit screen uses
the same page header, `Card` and status treatment. The property registry list and detail
screens use the page header, `DataTable`, `Card` and `StatusBadge`, with the key and
datatype in monospace. The create and amend screens put `Form`, `Field`, `Select` and
`Checkbox` in a `Card`, and the deprecate action is a danger `Button` that opens a `Dialog`.
The public entry page (issue #440) builds the detail layout
described under "Layout patterns" below: a breadcrumb, a title with a code chip and status
pill, and a two-column body. Its sidebar is a fixed 20rem wide, not one third of the page,
and it has no action buttons because a reader cannot edit. The audit trail pattern is not
yet implemented on any screen: the entry page's "Recent changes" is a plain dated list.
Read this as the destination, not a description of what exists today.

## Palette

| Role | Hex | Use |
|---|---|---|
| Paper | `#FFFFFF` | Page background |
| Ink | `#1E2A2C` | Primary text |
| Lab teal | `#2C6E6B` | Primary accent — links, primary actions, active states |
| Teal, deep | `#204E4C` | Hover/pressed states, deep accent text, dark panel fills |
| Specimen clay | `#A86A3A` | Status only (draft) — never a clickable action |
| Line / border | `#DCD5C6` | Borders, dividers, table rules |

### Semantic status colours (lifecycle, distinct from accent)

| Status | Text | Background |
|---|---|---|
| Draft | `#A86A3A` | `#F4E6D9` |
| In review | `#4F6D8C` | `#E4EAF0` |
| Published | `#3F7D4E` | `#E3EEE1` |
| Deprecated | `#6B6459` | `#ECE9E3` |

Muted supporting neutrals used throughout: `#5A6462` (secondary text), `#8B9391`
(tertiary/help text), `#4A5250` / `#6B7370` (mono code text), `#F1EDE2` (row hover),
`#EFEBE0` (code chip background).

## Type

| Role | Face | Used for |
|---|---|---|
| Display / headings | Source Serif 4 (weight 600) | Page titles, entry names |
| Interface & body | IBM Plex Sans (400/500/600) | Labels, buttons, body copy, nav |
| Data & codes | IBM Plex Mono (400/500) | SCTIDs, LOINC codes, versions, dates — always tabular figures, never truncated |

Sizes in use: headings 26–38px, body/UI 13–15px, helper/meta text 12–12.5px, mono code
12–13px.

## Layout patterns

- **App shell**: 56–60px top nav bar (serif wordmark left, primary nav links center-left,
  user avatar right), 1px `#DCD5C6` border beneath. Active nav link: teal underline +
  deep-teal text.
  Implemented in `shell/site-header.tsx` and `shell/user-menu.tsx`: the right-hand control is
  a user menu (the display name in a button, not an avatar). See
  [frontend-routing.md](frontend-routing.md) for its four auth states.
- **Page header**: serif title + secondary meta line (counts, version, status) left;
  primary action button right.
- **Filter bar**: search input + pill-style toggle buttons (rounded, 20px radius) for
  status filters, plus dropdown filters, all on one row. Active filters follow as
  removable chips with a "Clear all filters" button. A pressed pill carries a check mark
  and a heavier weight as well as its fill.
- **Search field**: a labelled input with a submit button, 40px tall, in a search
  landmark. The label stays visible; the field never relies on a placeholder to name it.
- **Pagination**: Previous and Next buttons, no page numbers and no total. On the last
  page it states "No more results" in text.
- **Dense data table**: uppercase 12px column headers (letter-spaced, muted), 1px row
  dividers, row hover fill `#F1EDE2`, code columns in mono left-aligned, numeric/date
  columns tabular-numeric right-aligned, status shown as pill (never colour alone).
- **Detail/edit page**: breadcrumb → title + code chip + status pill + action buttons,
  two-column body (main field card ~2/3 width, metadata + audit sidebar ~1/3 width).
- **Field pattern per FR-77 data type**: consistent card-style input row per type — free
  text (plain input), coded value (read-only display row with term + mono SCTID + "Change"
  button, validated against reference set), numeric-with-units and reference types should
  follow the same read-only-row-with-change-action pattern for consistency.
- **Cards/panels**: white `#FFFFFF` fill, 1px `#DCD5C6` border, 6px radius, 22–32px padding
  — used for form sections, metadata, and audit sidebar.
- **Audit trail**: vertical list, left border rule (`#DCD5C6`), mono timestamp + attributed
  actor above plain description — version-history style, not an activity feed.
- **Buttons**: primary = solid teal `#2C6E6B` fill / paper text, hover deep teal; secondary
  = transparent with `#DCD5C6` border, hover border+text turn teal; tertiary/text links =
  teal, underline on hover.
- **Hero/ceremony treatment**: reserved for sign-in (split panel, deep-teal side panel with
  serif headline + stats) and publish flows — everywhere else stays flat and quiet.
  Sign-in is built as the Keycloak login theme (issue #432): a deep-teal side panel with a
  serif headline and three factual lines, and no statistics, because the platform has no
  figures to show. See [authentication.md](authentication.md#the-login-theme).
- **Radius**: 4px for inputs/buttons/chips, 6px for cards, 20px (pill) for filter toggles
  and status pills.
- **Borders over shadows**: no drop shadows in the system; separation comes from 1px
  `#DCD5C6` borders/rules.

## Page layout

The design notes above give no container width, gutter, rhythm or title size. These
values are fixed here and implemented by `PageContainer`, `PageHeader` and `Card` in
`frontend/src/components/` (see [components.md](components.md#layout-primitives)):

- **Desktop-first.** The platform targets desktop and laptop screens. Phones are not a
  design target and nothing is laid out for one; a tablet is acceptable, not designed for.
  No component carries phone-specific behaviour or a responsive gutter.
- **Container**: one fixed maximum width of 1200px (the `--container-page` token in
  `app.css`), centred. There is no width option. The box is border-box, so the gutter
  sits inside the 1200px and content is at most 1152px wide.
- **Gutter and rhythm**: 24px horizontal gutter on the container; 24px between its
  children. The container sits inside `main`, which pads 1rem (16px) in `app.css`, so
  today content starts 40px from the viewport edge, not 24px. The app shell issue is
  where those two paddings get reconciled; until then, read 40px as the rendered value.
- **Card padding**: one value, 24px, inside the 22-32px range above. No padding option
  and no variants.
- **Page title**: `text-3xl`, 30px, inside the 26-38px range of the notes, in the serif
  face. Its meta line is 12px, in the muted text colour.

## Voice & content rules

- SCTIDs/codes: always monospace, always left-aligned, never ellipsis-truncated.
- Status always paired with a text label, never colour-only.
- No patient-record visual tropes (vitals, photos); no marketing gradients or stock
  imagery.

## How this maps to the platform

The notes above describe four status colours and reference a generic "status" concept.
The platform's real lifecycle (`STATUS_OPTIONS` in
`frontend/src/catalogue/status-options.ts`, matching
`nptc.catalogue.maintenance.MAINTENANCE_STATUSES` server-side) has four values: `draft`,
`active`, `deprecated`, `withdrawn`. Three reconciliations were needed to land the
`--color-status-*` tokens in `frontend/src/styles/app.css`:

- **Active reuses Published's colour pair**, and **Withdrawn reuses Deprecated's**. The
  notes name "Published" and have no "Withdrawn" entry; no separate colour was drafted for
  either, so each maps onto the nearest drafted pair rather than inventing a new one.
- **"In review" has no counterpart in the real lifecycle.** It stays in the palette table
  above, unused, rather than being silently dropped — a future status added to the
  lifecycle that means "in review" already has a drafted colour to reuse. Note for
  whoever does that: `#4F6D8C` on `#E4EAF0` is 4.44:1, below WCAG AA's 4.5:1 for
  `StatusBadge`'s 12px text (PR #327 review) — darken it before wiring it to a token,
  the same way Draft's and Active's pairs were below.
- **Draft's and Active's `*-text` values are darkened from this table's `#A86A3A` /
  `#3F7D4E`** to `#905B32` / `#3B7448` in the actual `--color-status-*` tokens
  (`frontend/src/styles/app.css`), within the same hue and saturation. As drafted they were
  3.59:1 and 4.13:1 against their backgrounds — both below the 4.5:1 `StatusBadge`'s 12px
  text needs. `frontend/tests/design-tokens-contrast.test.ts` asserts every status pair stays
  at or above 4.5:1, so a future edit can't reintroduce this.
- **A `neutral` tone was added**, with its own colour pair (not a reuse of Deprecated's),
  so every `StatusBadge` tone has a symmetrical entry in the token table. It is the
  fallback for a status value the mapping does not recognise (`statusToneFor` in
  `status-options.ts`), so a badge degrades to a muted pill rather than a component
  crashing over an unexpected string.
- **`--color-text-tertiary` is darkened from this table's `#8B9391`** to `#656C6B`
  (`frontend/src/styles/app.css`), within the same hue and saturation. As drafted it was
  3.14:1 on paper; a first fix (`#6E7774`, 4.61:1 on paper) still measured 3.94:1 on
  `--color-surface-sunken` - `DataTable`'s row-hover fill this PR adds - so meta/help text
  in a hovered row lost AA precisely while the pointer was over it (PR #327 second review).
  The current value clears 4.5:1 on both surfaces; `design-tokens-contrast.test.ts` checks
  both.

Danger/error keeps the platform's existing red pair (`--color-danger` /
`--color-danger-surface`); the notes specify no validation-error colour.

Fonts are self-hosted via `@fontsource` packages, not the Google Fonts `<link>` this
document's source draft implied — see
[ADR-0025](../adr/0025-frontend-styling.md#typography) for why.
