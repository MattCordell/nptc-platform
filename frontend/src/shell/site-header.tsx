import { Link } from "@tanstack/react-router";

import { UserMenu } from "./user-menu.tsx";

/**
 * Every link is shown to every visitor, including the authenticated and admin
 * sections. That is deliberate: NFR-20 says hiding a UI control is
 * presentation, not access control, and the real gate is the server-side
 * permission check every request goes through. Only the user menu varies with
 * the auth state, and it decides what to *offer*, never what is allowed.
 *
 * The active link is marked by the router's own `aria-current="page"` and
 * underlined, so it is distinguishable without colour (NFR-31).
 */
const NAV_LINK_CLASSES =
  "inline-block rounded-control px-1 py-1 text-sm font-medium text-[var(--color-text)] hover:underline aria-[current=page]:text-[var(--color-accent-hover)] aria-[current=page]:underline aria-[current=page]:decoration-2 aria-[current=page]:underline-offset-4";

const NAV_LINKS = [
  { to: "/catalogue", label: "Search the catalogue" },
  { to: "/releases", label: "Releases" },
  { to: "/submissions", label: "Submissions" },
  { to: "/interest", label: "My interest" },
  { to: "/admin", label: "Admin" },
  { to: "/account", label: "Account" },
] as const;

export function SiteHeader() {
  return (
    <header className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="max-w-page mx-auto flex min-h-14 flex-wrap items-center gap-x-6 gap-y-2 px-6 py-2">
        <Link
          to="/"
          activeOptions={{ exact: true }}
          className="font-display text-lg font-semibold text-[var(--color-text)]"
        >
          NPTC Catalogue
        </Link>
        <nav aria-label="Primary">
          <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0">
            {NAV_LINKS.map((link) => (
              <li key={link.to}>
                <Link to={link.to} className={NAV_LINK_CLASSES}>
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="ml-auto">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
