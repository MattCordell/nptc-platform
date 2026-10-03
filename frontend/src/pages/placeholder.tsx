import { Link } from "@tanstack/react-router";

import { BackToLandingLink } from "../components/back-to-landing-link.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { NoticePage } from "../components/notice-page.tsx";
import { StatusBadge } from "../components/status-badge.tsx";

export interface PlaceholderPageOptions {
  /** The page's `<h1>`. */
  title: string;
  /**
   * The GitHub issue that replaces this placeholder with the real screen.
   * Omitted for routes past the P1 horizon that P1-SEQUENCING.md doesn't
   * name an issue for yet.
   */
  issue?: number;
  /**
   * The nearest screen that is already built, offered as a way on. Only
   * worth setting where a built sibling exists: the admin stubs have
   * `/admin/catalogue`, while the public `/catalogue` is itself a stub.
   */
  nearest?: { to: string; label: string };
}

/**
 * A route's stand-in until its screen lands, used by every route in
 * `route-tree.ts` that has no page built yet. A factory rather than one file
 * per placeholder: swapping in a real screen is a one-line route-table edit
 * and a new page file, with no dead placeholder file to remember to delete.
 *
 * Named `create...`, not `Placeholder...`, so this module itself is not
 * treated as a component export by `react-refresh/only-export-components`.
 */
export function createPlaceholderPage({ title, issue, nearest }: PlaceholderPageOptions) {
  return function PlaceholderPage() {
    return (
      <NoticePage
        title={title}
        id="placeholder-heading"
        actions={
          <>
            <BackToLandingLink variant="primary" />
            {nearest === undefined ? null : (
              <Link to={nearest.to} className={buttonClassName("secondary")}>
                {nearest.label}
              </Link>
            )}
          </>
        }
      >
        <div>
          <StatusBadge tone="neutral" label="Planned" />
        </div>
        <p className="m-0">This screen has not been built yet.</p>
        {issue === undefined ? null : <p className="m-0">Planned with issue #{issue}.</p>}
      </NoticePage>
    );
  };
}
