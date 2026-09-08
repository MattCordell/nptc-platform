import { useLocation } from "@tanstack/react-router";

import { useSession } from "../api/queries.ts";
import { useAuth } from "../auth/session.ts";

/**
 * The one literal `acr_values` on the frontend (issue #184, NFR-06) -
 * everywhere else, the value comes from the server's own RFC 9470 challenge
 * (`nptc/api/step-up.ts`'s `parseStepUpChallenge`), never a constant here.
 * This banner is the one exception: it offers step-up *before* any refusal
 * has happened, so there is no challenge yet to read a value from. Changing
 * the realm's LoA mapping (`AuthSettings.mfa_acr_values`) needs this
 * constant updated to match - a narrower promise than the reactive path's,
 * and recorded as such in `docs/adr/0036-spa-step-up-loop.md`.
 */
const PRE_EMPTIVE_STEP_UP_ACR_VALUES = "2";

/**
 * Shown on every `/admin/*` screen (`admin-layout.tsx`) for a signed-in
 * administrator who has not yet completed the realm's second authentication
 * factor - so they can complete it before walking into a refusal, rather
 * than only after one (issue #184's last acceptance criterion).
 *
 * Renders nothing for every other case - signed out, unauthenticated,
 * already MFA-satisfied, or the session query still in flight - so a
 * loading flash never appears for the common case of an administrator who
 * is already stepped up.
 */
export function StepUpBanner() {
  const { signIn } = useAuth();
  const { data } = useSession();
  // The router's own current location, not `window.location`: the app's
  // memory-history test harness (`render-route.tsx`) never touches the
  // real `window.location`, and `RequireAuth` already reads a redirect
  // target the same way (`useLocation().href`).
  const location = useLocation();

  if (!data || !data.authenticated || data.mfa_satisfied) {
    return null;
  }

  return (
    <div
      role="status"
      className="flex items-center justify-between gap-4 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm text-[var(--color-text)]"
    >
      <p>
        Some administrative actions need an extra sign-in step, which you have not
        completed yet.
      </p>
      <button
        type="button"
        className="cursor-pointer rounded-md border border-[var(--color-border)] bg-transparent px-3 py-1 font-medium underline"
        onClick={() => {
          void signIn({
            acrValues: PRE_EMPTIVE_STEP_UP_ACR_VALUES,
            redirect: location.href,
          });
        }}
      >
        Verify now
      </button>
    </div>
  );
}
