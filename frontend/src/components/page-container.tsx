import type { ComponentPropsWithoutRef } from "react";

type PageContainerProps = ComponentPropsWithoutRef<"div">;

/**
 * The page-width wrapper every screen sits in: centred, capped at
 * `--container-page`, with one horizontal gutter and one vertical rhythm
 * between its children (docs/architecture/design-system.md). A `div`, not
 * `main` - the root layout already provides the one `<main>` landmark.
 * Desktop-first: it adds no breakpoint-specific behaviour.
 */
export function PageContainer({ className, ...rest }: PageContainerProps) {
  return (
    <div
      {...rest}
      className={["max-w-page mx-auto flex w-full flex-col gap-6 px-6", className]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
