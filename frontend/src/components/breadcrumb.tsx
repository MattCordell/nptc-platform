import { Children, type ReactNode } from "react";

type BreadcrumbProps = {
  /** The pages above this one, outermost first, each already a link. The
   * caller builds them with the router's typed `<Link>`, so this component
   * never holds a URL. */
  ancestors: ReactNode[];
  /** The current page. Plain text, marked `aria-current="page"`. */
  current: string;
};

/**
 * The trail from the catalogue down to the page on screen
 * (docs/architecture/design-system.md). The separator is drawn by the list
 * itself and hidden from assistive technology, so a screen reader hears an
 * ordered list of places, not a row of slashes.
 */
export function Breadcrumb({ ancestors, current }: BreadcrumbProps) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="m-0 flex list-none flex-wrap items-center gap-x-2 p-0 text-sm text-[var(--color-text-muted)]">
        {Children.toArray(ancestors).map((ancestor, index) => (
          <li key={index} className="flex items-center gap-2">
            <span className="text-[var(--color-accent)] [&_a]:inline-flex [&_a]:min-h-6 [&_a]:items-center [&_a]:hover:underline">
              {ancestor}
            </span>
            <span aria-hidden="true">/</span>
          </li>
        ))}
        <li aria-current="page" className="inline-flex min-h-6 items-center">
          {current}
        </li>
      </ol>
    </nav>
  );
}
