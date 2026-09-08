import { useSession } from "../api/queries.ts";
import { requestStepUp } from "../auth/step-up.tsx";

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
 * Shown on every `/admin/*` screen (`admin-layout.tsx`) for any signed-in
 * user who has not completed the realm's second authentication factor - so
 * an administrator can complete it before walking into a refusal, rather
 * than only after one (issue #184's last acceptance criterion).
 *
 * Gated on `mfa_satisfied` alone, per `SessionResponse`'s own contract
 * (`nptc.api.routers.auth`) - not on holding the Administrator role, which
 * `GET /auth/me` cannot answer for a suppressed administrator anyway:
 * `principal_for` structurally drops a role suppressed for want of MFA from
 * `roles` before it is ever serialised (NFR-06), so a signed-in
 * administrator who needs this banner and an ordinary member who does not
 * hold the role at all are indistinguishable from `roles` alone. A visitor
 * with no administrative permission sees the same offer and can harmlessly
 * ignore it - the copy says "some administrative actions", not "you".
 *
 * Renders nothing for every other case - signed out, unauthenticated,
 * already MFA-satisfied, or the session query still in flight - so a
 * loading flash never appears for the common case of a user who is already
 * stepped up.
 *
 * "Verify now" goes through `requestStepUp` (PR #284 review), not a direct
 * `signIn` redirect: that gives the pre-emptive path the same silent-first
 * treatment as the reactive one - a click that the SSO session can satisfy
 * without interaction closes the banner with no navigation at all, and
 * only a click that genuinely needs Keycloak's help shows the same
 * "you're about to be sent to sign in again" dialog `StepUpController`
 * already shows for a reactive challenge, rather than redirecting
 * unannounced.
 */
export function StepUpBanner() {
  const { data } = useSession();

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
          requestStepUp(PRE_EMPTIVE_STEP_UP_ACR_VALUES);
        }}
      >
        Verify now
      </button>
    </div>
  );
}
