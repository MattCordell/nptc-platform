export interface PlaceholderPageOptions {
  /** The page's `<h1>`. */
  title: string;
  /**
   * The GitHub issue that replaces this placeholder with the real screen.
   * Omitted for routes past the P1 horizon that P1-SEQUENCING.md doesn't
   * name an issue for yet.
   */
  issue?: number;
}

/**
 * A route's stand-in until its screen lands, used by every route in
 * `route-tree.ts` that has no page built yet. A factory rather than one file
 * per placeholder: swapping in a real screen is a one-line route-table edit
 * and a new page file, with no dead placeholder file to remember to delete.
 *
 * Named `create...`, not `Placeholder...`, so this module itself is not
 * treated as a component export by `react-refresh/only-export-components`.
 */
export function createPlaceholderPage({ title, issue }: PlaceholderPageOptions) {
  return function PlaceholderPage() {
    return (
      <section aria-labelledby="placeholder-heading">
        <h1 id="placeholder-heading">{title}</h1>
        <p>
          {issue === undefined ? (
            <>This screen has not been built yet.</>
          ) : (
            <>This screen has not been built yet. It lands with issue #{issue}.</>
          )}
        </p>
      </section>
    );
  };
}
