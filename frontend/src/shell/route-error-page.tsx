import { Link, useRouter, type ErrorComponentProps } from "@tanstack/react-router";
import { useEffect } from "react";

/**
 * The router-level `defaultErrorComponent`, catching any render error thrown
 * inside a route. PRD SS17.2 item 5: errors surfaced to the user say what to
 * do next, never a stack trace or an HTTP status - so this component must
 * never render `error.message`, `error.stack`, or any status code. The
 * detail still needs to reach a developer, so it goes to `console.error`
 * instead (see `route-error-page.test.tsx`, which asserts both halves: the
 * friendly message is shown, and the exception text is absent from the DOM).
 */
export function RouteErrorPage({ error, reset }: ErrorComponentProps) {
  const router = useRouter();

  useEffect(() => {
    console.error("Route render error", error);
  }, [error]);

  return (
    <section aria-labelledby="route-error-heading">
      <h1 id="route-error-heading">Something went wrong on this page</h1>
      <p>
        The page didn&apos;t load. Try again - if it keeps happening, go back to the
        catalogue and report the problem along with the address you were using.
      </p>
      <button
        type="button"
        onClick={() => {
          reset();
          void router.invalidate();
        }}
      >
        Try again
      </button>
      <Link to="/catalogue">Search the catalogue</Link>
    </section>
  );
}
