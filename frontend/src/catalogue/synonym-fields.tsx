import { Button } from "../components/button.tsx";
import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { ADD_SYNONYMS_FIELD_ID, addedTerms, synonymFieldId } from "./synonym-state.ts";
import type { SynonymRow } from "./synonym-state.ts";

/**
 * The entry's RCPA Synonyms, inside the edit form (FR-04, FR-36).
 *
 * Each active synonym is a text box. Remove marks a synonym for retirement
 * and the form's Save performs it, so one Save and one changelog note cover
 * every change. A retired synonym is not deleted: the platform keeps it and
 * its history. A separate box adds terms, and takes a cell pasted straight
 * from the legacy workbook, delimiters and all.
 */

export function SynonymFields({
  rows,
  retiredTerms,
  addText,
  rowErrors,
  addError,
  onRowsChange,
  onAddTextChange,
  onReinstate,
}: {
  rows: SynonymRow[];
  /** Synonyms the entry holds as retired. Each can be reinstated. */
  retiredTerms: string[];
  addText: string;
  /** The reason a row's change was refused or is invalid, by row id. */
  rowErrors: Record<string, string>;
  addError: string | undefined;
  onRowsChange: (next: SynonymRow[]) => void;
  onAddTextChange: (text: string) => void;
  onReinstate: (term: string) => void;
}) {
  const terms = addedTerms(addText);

  function update(id: string, change: Partial<SynonymRow>) {
    onRowsChange(rows.map((row) => (row.id === id ? { ...row, ...change } : row)));
  }

  return (
    <div className="flex flex-col gap-3">
      <h3>RCPA Synonyms</h3>
      {rows.length === 0 && <p className="m-0">This entry has no RCPA Synonyms.</p>}
      {rows.map((row, index) =>
        row.removed ? (
          <div key={row.id} className="flex flex-wrap items-center gap-3">
            <p className="m-0">
              <del>{row.original}</del> will be removed when you save. The term is kept as
              a retired synonym.
            </p>
            {rowErrors[row.id] !== undefined && (
              <p className="m-0 text-sm text-[var(--color-danger)]">
                {rowErrors[row.id]}
              </p>
            )}
            <Button
              type="button"
              variant="secondary"
              aria-label={`Keep ${row.original}`}
              onClick={() => update(row.id, { removed: false })}
            >
              Keep
            </Button>
          </div>
        ) : (
          <div key={row.id} className="flex flex-wrap items-end gap-3">
            <Field
              id={synonymFieldId(row.id)}
              label={`Synonym ${index + 1}`}
              error={rowErrors[row.id]}
              className="min-w-64 flex-1"
            >
              {(controlProps) => (
                <input
                  {...controlProps}
                  className={INPUT_CLASSES}
                  type="text"
                  value={row.term}
                  onChange={(event) => update(row.id, { term: event.target.value })}
                />
              )}
            </Field>
            <Button
              type="button"
              variant="danger"
              aria-label={`Remove ${row.original}`}
              onClick={() => update(row.id, { removed: true })}
            >
              Remove
            </Button>
          </div>
        ),
      )}

      {retiredTerms.length > 0 && (
        <div>
          <h4>Retired synonyms</h4>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {retiredTerms.map((term) => (
              <li key={term} className="flex flex-wrap items-center gap-3">
                <span>{term}</span>
                <Button
                  type="button"
                  variant="secondary"
                  aria-label={`Reinstate ${term}`}
                  onClick={() => onReinstate(term)}
                >
                  Reinstate
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Field
        id={ADD_SYNONYMS_FIELD_ID}
        label="Add synonyms"
        hint="Type one term, or paste a cell straight from the spreadsheet. Separate several with a semicolon."
        error={addError}
      >
        {(controlProps) => (
          <input
            {...controlProps}
            className={INPUT_CLASSES}
            type="text"
            value={addText}
            onChange={(event) => onAddTextChange(event.target.value)}
          />
        )}
      </Field>
      {/* Not a live region: it changes on every keystroke, and a screen-reader
          editor would be interrupted once per character. The result is announced
          once, after the save. */}
      <p className="m-0">
        {addText.trim().length === 0
          ? "No new synonyms yet."
          : terms.length === 0
            ? "Nothing to add. A cell of only delimiters adds no terms."
            : `Save will add ${terms.length} ${terms.length === 1 ? "term" : "terms"}: ${terms
                .map((term) => `“${term}”`)
                .join(", ")}`}
      </p>
    </div>
  );
}
