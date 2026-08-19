import { useEffect } from "react";

/**
 * Every route sets its own document title via the route table's `head`
 * option (see `route-tree.ts`'s `titled()` helper), rendered by
 * `<HeadContent />` in `root-layout.tsx`. The router's `defaultNotFoundComponent`
 * and `defaultErrorComponent` sit outside that per-route mechanism, though -
 * they render in place of whatever route was requested, not as a route of
 * their own - so without this, a deep link straight to an unknown or
 * erroring URL leaves the title at whatever the last real navigation set
 * (or blank, on a cold load).
 */
export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
