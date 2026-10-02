import { Children, type ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  id?: string;
};

// Not a truthiness check: a meta of 0 (an empty count) must still render. An
// array that maps to nothing counts as empty too, so no empty wrapper renders.
function hasContent(node: ReactNode): boolean {
  return Children.toArray(node).some((child) => child !== "");
}

/**
 * A screen's title block (docs/architecture/design-system.md): the page's
 * one `h1`, an optional muted meta line, and optional actions on the right.
 * `id` lands on the `h1` so a caller's `aria-labelledby` keeps working.
 * Deliberately not a `<header>` element: `app.css` pads every `header`
 * globally, and a page title is not the page's banner landmark.
 */
export function PageHeader({ title, meta, actions, id }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex flex-col gap-1">
        <h1 id={id} className="m-0 text-3xl leading-tight text-[var(--color-text)]">
          {title}
        </h1>
        {hasContent(meta) ? (
          <div className="text-xs text-[var(--color-text-muted)]">{meta}</div>
        ) : null}
      </div>
      {hasContent(actions) ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
