import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";

import { useSession } from "../api/queries.ts";
import { useAuthStatus } from "../auth/auth-status.ts";
import { buttonClassName } from "../components/button-class-name.ts";

const MENU_ITEM_CLASSES =
  "block rounded-control px-3 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-sunken)] hover:underline";

const FALLBACK_LABEL = "Your account";

/**
 * Mounted only when the status is `signed-in`, so `useSession` never fires
 * for a signed-out or unavailable visitor. The menu is a disclosure, not an
 * ARIA `menu`: it is a button that shows a short list of links, so Tab moves
 * through them and no arrow-key handling is promised.
 *
 * `openPath` records the pathname the menu was opened on rather than a plain
 * boolean, so any navigation closes it without an effect that resets state.
 */
function SignedInMenu() {
  const session = useSession();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;
  const panelId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const user = session.data?.user;
  const label = user?.display_name || user?.username || FALLBACK_LABEL;

  const close = () => setOpenPath(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const closeIfOutside = (event: Event) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpenPath(null);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenPath(null);
        buttonRef.current?.focus();
      }
    };
    // `pointerdown` as well as `focusin`: browsers that do not focus a button
    // on click (Safari) would otherwise leave the panel open after a click
    // elsewhere, and Tab moving out of the panel needs `focusin`.
    document.addEventListener("pointerdown", closeIfOutside);
    document.addEventListener("focusin", closeIfOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeIfOutside);
      document.removeEventListener("focusin", closeIfOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpenPath(open ? null : pathname)}
        className="rounded-control inline-flex h-9 max-w-56 items-center gap-2 border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-text)] hover:border-[var(--color-accent)]"
      >
        <span className="truncate">{label}</span>
        <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="rounded-card absolute top-full right-0 z-50 mt-1 min-w-48 border border-[var(--color-border)] bg-[var(--color-surface)] p-1 shadow-md"
      >
        <ul className="m-0 list-none p-0">
          <li>
            <Link to="/account" className={MENU_ITEM_CLASSES} onClick={close}>
              Account
            </Link>
          </li>
          <li>
            <Link to="/sign-out" className={MENU_ITEM_CLASSES} onClick={close}>
              Sign out
            </Link>
          </li>
        </ul>
      </div>
    </div>
  );
}

/**
 * The header's account control, in the same footprint in every state so the
 * bar does not shift when the status settles. `restoring` renders an inert
 * placeholder rather than the signed-out links, which a returning user never
 * meant to see; `unavailable` is plain text, not a live region, because it is
 * a standing condition rather than an event (and a second `role="status"`
 * would collide with the step-up banner).
 *
 * Nothing here decides access: the links are offers, and the server
 * authorises every request (NFR-20).
 */
export function UserMenu() {
  const status = useAuthStatus();

  switch (status) {
    case "restoring":
      return <div aria-hidden="true" className="h-9 w-40" />;
    case "signed-out":
      return (
        <div className="flex min-h-9 items-center gap-3">
          <Link to="/sign-in" className={buttonClassName("secondary")}>
            Sign in
          </Link>
          <Link to="/register" className={buttonClassName("primary")}>
            Register
          </Link>
        </div>
      );
    case "signed-in":
      return <SignedInMenu />;
    case "unavailable":
      return (
        <p className="m-0 flex h-9 items-center text-sm text-[var(--color-text-muted)]">
          Sign-in unavailable
        </p>
      );
  }
}
