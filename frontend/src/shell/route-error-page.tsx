import { Link, useRouter, type ErrorComponentProps } from "@tanstack/react-router";
import { useEffect } from "react";

import { BackToLandingLink } from "../components/back-to-landing-link.tsx";
import { Button } from "../components/button.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { NoticePage } from "../components/notice-page.tsx";
import { useDocumentTitle } from "./use-document-title.ts";

/**
 * The router-level `defaultErrorComponent`, catching any render error thrown
 * inside a route. PRD SS17.2 item 5: errors surfaced to the user say what to
 * do next, never a stack trace or an HTTP status - so this component must
 * never render `error.message`, `error.stack`, or any status code. The
 * detail still needs to reach a developer, so it goes to `console.error`
 * instead (see `router.test.tsx`'s "route error boundary" tests, which
 * assert both halves: the friendly message is shown, and the exception text
 * is absent from the DOM).
 */
export function RouteErrorPage({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  useDocumentTitle("Something went wrong — NPTC Catalogue");

  useEffect(() => {
    console.error("Route render error", error);
  }, [error]);

  return (
    <NoticePage
      title="Something went wrong on this page"
      id="route-error-heading"
      focusHeading
      actions={
        <>
          <Button
            type="button"
            onClick={() => {
              reset();
              void router.invalidate();
            }}
          >
            Try again
          </Button>
          <BackToLandingLink />
          <Link to="/catalogue" className={buttonClassName("secondary")}>
            Search the catalogue
          </Link>
        </>
      }
    >
      <p className="m-0">
        The page didn&apos;t load. Try again - if it keeps happening, go back to the
        catalogue and report the problem along with the address you were using.
      </p>
    </NoticePage>
  );
}
