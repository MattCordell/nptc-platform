import { Link } from "@tanstack/react-router";

/**
 * The primary navigation is shown unconditionally, including the
 * authenticated and admin sections. That is deliberate: NFR-20 says hiding a
 * UI control is presentation, not access control, and the real gate is the
 * server-side permission check every request goes through. Once #41 lands
 * real sign-in, this can be trimmed for a better *experience* (no point
 * advertising a link an anonymous visitor cannot use yet), but that is a
 * usability improvement, never the access-control mechanism itself.
 */
export function SiteHeader() {
  return (
    <header>
      <Link to="/">NPTC Catalogue</Link>
      <nav aria-label="Primary">
        <ul>
          <li>
            <Link to="/catalogue">Search the catalogue</Link>
          </li>
          <li>
            <Link to="/releases">Releases</Link>
          </li>
          <li>
            <Link to="/submissions">Submissions</Link>
          </li>
          <li>
            <Link to="/interest">My interest</Link>
          </li>
          <li>
            <Link to="/admin">Admin</Link>
          </li>
          <li>
            <Link to="/account">Account</Link>
          </li>
        </ul>
      </nav>
    </header>
  );
}
