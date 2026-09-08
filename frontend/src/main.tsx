import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createQueryClient } from "./api/query-client.ts";
import { AuthProvider } from "./auth/auth-context.tsx";
import { stepUpChallengeHandler } from "./auth/step-up.tsx";
import { createAppRouter } from "./router/router.tsx";
import "./styles/app.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found in index.html");
}

const router = createAppRouter();
// `stepUpChallengeHandler` forwards to whichever `StepUpController` is
// currently mounted (`RootLayout`, inside the router - see that module's
// own docstring for why it cannot be a prop passed from here) (issue #184,
// NFR-06).
const queryClient = createQueryClient(stepUpChallengeHandler);

createRoot(container).render(
  <StrictMode>
    {/*
      AuthProvider wraps the router, not a route: the session must exist
      before the first route renders, since `RequireAuth` reads it while
      deciding whether to redirect. QueryClientProvider wraps both: a query
      hook may itself read `useAuth()` (issue #147's `useApiClient`), so the
      auth context must already exist wherever a query can run.
    */}
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
