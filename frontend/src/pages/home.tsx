import { Link } from "@tanstack/react-router";

import { buttonClassName } from "../components/button.tsx";
import { useAuth } from "../auth/session.ts";

/**
 * The actions row is keyed on `useAuth().status`, not a boolean: `restoring`
 * and `unavailable` both show nothing, deliberately, rather than falling
 * back to a signed-out-looking default. `restoring` would otherwise flash
 * register/sign-in links a returning user never meant to see, and
 * `unavailable` is a config/outage state that should not invite a sign-in
 * attempt that cannot succeed.
 */
function HomeActions() {
  const { status } = useAuth();

  if (status === "signed-out") {
    return (
      <p className="flex gap-3">
        <Link to="/register" className={buttonClassName("primary")}>
          Register
        </Link>
        <Link to="/sign-in" className={buttonClassName("secondary")}>
          Sign in
        </Link>
      </p>
    );
  }

  if (status === "signed-in") {
    return (
      <p>
        <Link to="/sign-out" className={buttonClassName("secondary")}>
          Sign out
        </Link>
      </p>
    );
  }

  return null;
}

const SECONDARY_LINK_CLASSES = "text-[var(--color-accent)] hover:underline";

export function HomePage() {
  return (
    <section aria-labelledby="home-heading">
      <h1 id="home-heading">NPTC Catalogue Maintenance Platform</h1>
      <p className="text-[var(--color-text-muted)]">
        The National Pathology Test Catalogue: the SPIA Requesting terminology, curated by
        RCPA-QAP, published by NCTS as a SNOMED CT reference set and FHIR ValueSet.
      </p>
      <HomeActions />
      <p>
        <Link to="/catalogue" className={SECONDARY_LINK_CLASSES}>
          Search the catalogue
        </Link>
      </p>
      <p>
        <Link to="/about" className={SECONDARY_LINK_CLASSES}>
          About the catalogue
        </Link>
      </p>
    </section>
  );
}
