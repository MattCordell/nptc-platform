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
