import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthProvider } from "./auth/auth-context.tsx";
import { createAppRouter } from "./router/router.tsx";
import "./shell/shell.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found in index.html");
}

const router = createAppRouter();

createRoot(container).render(
  <StrictMode>
    {/*
      AuthProvider wraps the router, not a route: the session must exist
      before the first route renders, since `RequireAuth` reads it while
      deciding whether to redirect.
    */}
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </StrictMode>,
);
