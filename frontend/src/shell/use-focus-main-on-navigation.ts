import { useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, type RefObject } from "react";

/**
 * After a client-side navigation, a screen-reader or keyboard user is
 * otherwise left focused wherever they were on the previous page - there is
 * no full page load to reset focus the way there would be for a traditional
 * multi-page site. Moving focus to `<main>` on every route change is the
 * standard SPA fix for that (NFR-31, PRD 17.2 item 4).
 *
 * Guards on the *pathname itself* rather than a "have we run yet" boolean:
 * a boolean flips permanently on the first commit and never resets, so under
 * `StrictMode` (enabled in `main.tsx`) React's mount -> effect -> cleanup ->
 * effect-again sequence set it on the first pass and then unconditionally
 * moved focus on the second - stealing focus on the very first render, not
 * just after a real navigation. Comparing against the previous pathname is
 * correct in both worlds: unchanged on the double-invoked initial effect (no
 * focus move), changed only when the route actually did.
 *
 * Leaves focus alone when it is already inside `<main>`. A screen that moves
 * focus to its own heading (not-found, route error) does so in a child
 * effect, which React runs before this parent one when both commit together.
 * Whether they do depends on the router's commit timing, so the order is
 * made explicit here rather than relied on.
 */
export function useFocusMainOnNavigation(mainRef: RefObject<HTMLElement | null>) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const previousPathname = useRef(pathname);

  useEffect(() => {
    if (previousPathname.current === pathname) {
      return;
    }
    previousPathname.current = pathname;
    const main = mainRef.current;
    if (main === null || main.contains(document.activeElement)) {
      return;
    }
    main.focus();
  }, [pathname, mainRef]);
}
