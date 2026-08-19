import { Link } from "@tanstack/react-router";

/**
 * Rendered for any URL that matches no route (`notFoundMode: "fuzzy"` in
 * `router.tsx` means the nearest matching ancestor renders this, so the
 * shell - header, navigation, footer - stays on screen and the user has
 * somewhere to go, rather than a blank screen).
 */
export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-heading">
      <h1 id="not-found-heading">We couldn&apos;t find that page</h1>
      <p>
        The address may be mistyped, or the page may have moved. Catalogue entries are
        addressed by their NPTC identifier, for example{" "}
        <code>/catalogue/NPTC-000247</code>.
      </p>
      <ul>
        <li>
          <Link to="/catalogue">Search the catalogue</Link>
        </li>
        <li>
          <Link to="/">Go to the home page</Link>
        </li>
      </ul>
    </section>
  );
}
