import type { ReactNode } from "react";

import { Card } from "../components/card.tsx";

/**
 * One titled block on the public entry page. The `h2` is what a screen reader
 * user jumps between, so every block has one.
 */
export function EntrySection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-3">
      <h2 className="m-0 text-xl text-[var(--color-text)]">{title}</h2>
      {children}
    </Card>
  );
}

/**
 * Lets a wide table scroll inside the card at a narrow width, not widen the
 * page. A scrollable area has to take focus, or a keyboard user cannot scroll
 * it, so it is a focusable, named region.
 */
export function ScrollRegion({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    // The rule would have this region unfocusable, but axe's
    // `scrollable-region-focusable` requires focus on anything that scrolls.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div role="region" aria-label={label} tabIndex={0} className="overflow-x-auto">
      {children}
    </div>
  );
}
