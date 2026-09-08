import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { asStepUpChallenge, type StepUpChallenge } from "./step-up.ts";

/**
 * What a query/mutation cache handler is told about the request an
 * `insufficient_user_authentication` challenge (issue #184, NFR-06) came
 * back on.
 *
 * A query carries `queryHash` and `retry` (re-running the refused read in
 * place once the step-up succeeds); a mutation carries neither - see
 * `docs/adr/0036-spa-step-up-loop.md` for why a refused *write* is never
 * replayed automatically.
 */
export type StepUpChallengeContext =
  { kind: "query"; queryHash: string; retry: () => void } | { kind: "mutation" };

export type StepUpChallengeHandler = (
  challenge: StepUpChallenge,
  context: StepUpChallengeContext,
) => void;

/**
 * The shared TanStack Query defaults (issue #147).
 *
 * A factory rather than a module-level singleton, so `main.tsx` and
 * `src/test/render-route.tsx` cannot drift apart, and so every test gets its
 * own cache - a shared client would leak state between tests, and StrictMode
 * double-mounts every route it renders.
 *
 * `retry: false` for the same reason: this app's principal failure mode is
 * "the caller isn't authorised" or "the resource doesn't exist" (NFR-08's
 * audited 4xx paths), not a flaky network - retrying those just delays the
 * error the UI needs to show, and makes tests slower and more prone to
 * timing flakiness under StrictMode.
 *
 * `onStepUpChallenge` is the one seam issue #184's step-up detection needs:
 * every failed query and mutation passes through this cache's `onError`
 * regardless of which screen made the call, so a step-up challenge is
 * recognised here once rather than at each of #147's call sites. The
 * callback itself is optional and does nothing by default - a caller (only
 * `main.tsx` and `render-route.tsx`) wires it to `auth.stepUp`/the step-up
 * controller; a test that does not care about step-up simply omits it.
 */
export function createQueryClient(
  onStepUpChallenge?: StepUpChallengeHandler,
): QueryClient {
  const notify = (error: unknown, context: StepUpChallengeContext): void => {
    const challenge = asStepUpChallenge(error);
    if (challenge && onStepUpChallenge) {
      onStepUpChallenge(challenge, context);
    }
  };

  const queryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        notify(error, {
          kind: "query",
          queryHash: query.queryHash,
          retry: () => {
            void queryClient.refetchQueries({ queryKey: query.queryKey, exact: true });
          },
        });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        notify(error, { kind: "mutation" });
      },
    }),
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return queryClient;
}
