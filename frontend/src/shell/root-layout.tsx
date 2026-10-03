import { HeadContent, Outlet } from "@tanstack/react-router";
import { useRef } from "react";

import { StepUpController } from "../auth/step-up.tsx";
import { SiteFooter } from "./site-footer.tsx";
import { SiteHeader } from "./site-header.tsx";
import { SkipLink } from "./skip-link.tsx";
import { useFocusMainOnNavigation } from "./use-focus-main-on-navigation.ts";

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
      {/* Issue #184: reacts to any RFC 9470 step-up challenge, from any
          route. Mounted once, here, rather than per-screen - see
          `step-up.tsx`'s own docstring for why it lives inside the router
          tree rather than being passed down from `main.tsx`. */}
      <StepUpController />
    </>
  );
}
