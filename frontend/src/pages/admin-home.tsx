import { Link } from "@tanstack/react-router";

import { useSession } from "../api/queries.ts";
import { Card } from "../components/card.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { StatusBadge } from "../components/status-badge.tsx";

/**
 * The admin home (NFR-20, NFR-31). One card per `/admin/*` route, built or
 * planned. A planned card links to its stub page, so every admin route is
 * reachable without typing a URL.
 */

interface AdminScreen {
  to:
    | "/admin/catalogue"
    | "/admin/properties"
    | "/admin/audit"
    | "/admin/users"
    | "/admin/validation"
    | "/admin/releases"
    | "/admin/exports/config";
  title: string;
  description: string;
  planned: boolean;
}

const ADMIN_SCREENS: readonly AdminScreen[] = [
  {
    to: "/admin/catalogue",
    title: "Catalogue administration",
    description: "Find, filter and edit catalogue entries, or reclassify them in bulk.",
    planned: false,
  },
  {
    to: "/admin/properties",
    title: "Property registry",
    description: "Define the properties that describe catalogue entries.",
    planned: false,
  },
  {
    to: "/admin/audit",
    title: "Audit log",
    description: "See who changed what and when, and export a filtered view.",
    planned: false,
  },
  {
    to: "/admin/users",
    title: "User administration",
    description: "Manage user accounts and roles.",
    planned: true,
  },
  {
    to: "/admin/validation",
    title: "Validation findings",
    description: "Review the problems found in catalogue entries.",
    planned: true,
  },
  {
    to: "/admin/releases",
    title: "Release administration",
    description: "Cut and publish catalogue releases.",
    planned: true,
  },
  {
    to: "/admin/exports/config",
    title: "Export configuration",
    description: "Set how the catalogue is exported.",
    planned: true,
  },
];

const ADMINISTRATOR_ROLE = "administrator";

/**
 * Presentation only (NFR-20): the server refuses every action a caller may not
 * take, and no card is hidden. The message waits for a settled, authenticated
 * session, so a pending or failed read shows no wrong claim.
 *
 * `GET /auth/me` leaves the administrator role out of `roles` until the second
 * sign-in factor is done (NFR-06), so a real administrator can reach this
 * message. Their next step is the step-up offer in `StepUpBanner`.
 */
function RoleNotice() {
  const { data } = useSession();

  if (!data || !data.authenticated || data.roles.includes(ADMINISTRATOR_ROLE)) {
    return null;
  }

  const stepUpPending = !data.mfa_satisfied;

  return (
    <div
      role="status"
      className="flex max-w-3xl flex-col gap-2 rounded-[var(--radius-card)] border border-[var(--color-border)] bg-[var(--color-surface-sunken)] p-4 text-[var(--color-text)]"
    >
      <p className="m-0 font-medium">
        {stepUpPending
          ? "This session does not include the administrator role."
          : "Your account does not have the administrator role."}
      </p>
      <p className="m-0">
        {stepUpPending
          ? "If you are an administrator, complete the extra sign-in step at the top of this page, then reload. Otherwise the server will refuse most actions on these screens."
          : "The server will refuse most actions on these screens. Ask an administrator to grant you the role."}
      </p>
    </div>
  );
}

export function AdminHomePage() {
  return (
    <PageContainer className="py-6">
      <PageHeader
        id="admin-home-heading"
        title="Administration"
        meta="Choose a screen to work on."
      />
      <RoleNotice />
      <nav aria-label="Administration screens">
        <ul className="m-0 grid list-none grid-cols-1 gap-6 p-0 md:grid-cols-2 lg:grid-cols-3">
          {ADMIN_SCREENS.map((screen) => (
            <li key={screen.to} className="flex">
              <Card className="flex w-full flex-col gap-3">
                <h2 className="m-0 text-xl">
                  <Link
                    to={screen.to}
                    className="inline-flex min-h-6 items-center text-[var(--color-accent)] hover:underline"
                  >
                    {screen.title}
                  </Link>
                </h2>
                <p className="m-0 text-[var(--color-text-muted)]">{screen.description}</p>
                {screen.planned ? (
                  <div>
                    <StatusBadge tone="neutral" label="Planned" />
                  </div>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      </nav>
    </PageContainer>
  );
}
