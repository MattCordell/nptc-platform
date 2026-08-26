import { HeadContent, Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, type RefObject } from "react";

import { SiteFooter } from "./site-footer.tsx";
import { SiteHeader } from "./site-header.tsx";
import { SkipLink } from "./skip-link.tsx";

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
 */
function useFocusMainOnNavigation(mainRef: RefObject<HTMLElement | null>) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const previousPathname = useRef(pathname);

  useEffect(() => {
    if (previousPathname.current === pathname) {
      return;
    }
    previousPathname.current = pathname;
    mainRef.current?.focus();
  }, [pathname, mainRef]);
}

/**
 * The page chrome every route renders inside: header, primary navigation,
 * main landmark, footer. Deliberately no `<h1>` here - each page owns its
 * own, so heading order stays sane as screens are added.
 *
 * Styling comes from `src/styles/app.css` (issue #148, ADR-0025) - see
 * `docs/architecture/components.md` for the component baseline built on it.
 */
export function RootLayout() {
  const mainRef = useRef<HTMLElement>(null);
  useFocusMainOnNavigation(mainRef);

  return (
    <>
      <HeadContent />
      <SkipLink />
      <SiteHeader />
      <main id="main-content" tabIndex={-1} ref={mainRef}>
        <Outlet />
      </main>
      <SiteFooter />
    </>
  );
}
