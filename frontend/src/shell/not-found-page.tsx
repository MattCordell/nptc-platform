import { Link } from "@tanstack/react-router";

import { BackToLandingLink } from "../components/back-to-landing-link.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { NoticePage } from "../components/notice-page.tsx";
import { useDocumentTitle } from "./use-document-title.ts";

/**
 * Rendered for any URL that matches no route (`notFoundMode: "fuzzy"` in
 * `router.tsx` means the nearest matching ancestor renders this, so the
 * shell - header, navigation, footer - stays on screen and the user has
 * somewhere to go, rather than a blank screen). It replaces the page the
 * user asked for, so it moves focus to its own heading.
 */
export function NotFoundPage() {
  useDocumentTitle("Page not found — NPTC Catalogue");

  return (
    <NoticePage
      title="We couldn't find that page"
      id="not-found-heading"
      focusHeading
      actions={
        <>
          <BackToLandingLink variant="primary" />
          <Link to="/catalogue" className={buttonClassName("secondary")}>
            Search the catalogue
          </Link>
        </>
      }
    >
      <p className="m-0">
        The address may be mistyped, or the page may have moved. Catalogue entries are
        addressed by their NPTC identifier, for example{" "}
        <code>/catalogue/NPTC-000247</code>.
      </p>
    </NoticePage>
  );
}
