import type { ReactNode } from "react";

type DetailLayoutProps = {
  /** The main column. */
  children: ReactNode;
  /** The narrower column of metadata beside it. */
  sidebar: ReactNode;
  /** Names the sidebar's landmark, e.g. "Entry details". */
  sidebarLabel: string;
};

/**
 * The two-column body of a detail screen (docs/architecture/design-system.md):
 * a wide main column and a fixed-width sidebar. Below the large breakpoint the
 * sidebar stacks under the main column, in the same reading order.
 * `minmax(0, 1fr)` lets the main column shrink, so a wide table inside it
 * scrolls itself rather than widening the page.
 */
export function DetailLayout({ children, sidebar, sidebarLabel }: DetailLayoutProps) {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="flex min-w-0 flex-col gap-6">{children}</div>
      <aside aria-label={sidebarLabel} className="flex min-w-0 flex-col gap-6">
        {sidebar}
      </aside>
    </div>
  );
}
