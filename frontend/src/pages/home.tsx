import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "../auth/session.ts";
import { Button } from "../components/button.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { PageContainer } from "../components/page-container.tsx";

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
      <nav aria-label="Account actions" className="flex gap-3">
        <Link to="/register" className={buttonClassName("primary")}>
          Register
        </Link>
        <Link to="/sign-in" className={buttonClassName("secondary")}>
          Sign in
        </Link>
      </nav>
    );
  }

  if (status === "signed-in") {
    return (
      <nav aria-label="Account actions" className="flex gap-3">
        <Link to="/sign-out" className={buttonClassName("secondary")}>
          Sign out
        </Link>
      </nav>
    );
  }

  return null;
}

const LINK_CLASSES = "text-[var(--color-accent)] hover:underline";

/**
 * The contribute card follows the same status keying as `HomeActions`. Its
 * link is named "Register to contribute", not "Register": two links with one
 * name to one route are indistinguishable in a screen reader's list of links.
 */
function ContributeCard() {
  const { status } = useAuth();

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="m-0 text-xl">How to contribute</h2>
      {status === "signed-in" ? (
        <>
          <p className="m-0 text-[var(--color-text-muted)]">
            You can propose new tests and amendments from your submissions.
          </p>
          <Link to="/submissions" className={LINK_CLASSES}>
            My submissions
          </Link>
        </>
      ) : (
        <p className="m-0 text-[var(--color-text-muted)]">
          Registered members can propose new tests and amendments.
          {status === "signed-out" ? " Register to get started." : null}
        </p>
      )}
      {status === "signed-out" ? (
        <Link to="/register" className={LINK_CLASSES}>
          Register to contribute
        </Link>
      ) : null}
    </Card>
  );
}

function CatalogueSearchForm() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void navigate({ to: "/catalogue", search: { q: query.trim() } });
  }

  return (
    <form
      role="search"
      aria-label="Catalogue search"
      onSubmit={handleSubmit}
      className="flex items-end gap-3"
    >
      <Field label="Search by test name or code" className="flex-1">
        {(controlProps) => (
          <input
            {...controlProps}
            type="search"
            name="q"
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className={INPUT_CLASSES}
          />
        )}
      </Field>
      <Button type="submit" className="min-h-10">
        Search
      </Button>
    </form>
  );
}

export function HomePage() {
  return (
    <PageContainer className="py-6">
      <section aria-labelledby="home-heading" className="flex flex-col gap-6">
        <div className="flex max-w-3xl flex-col gap-3">
          <h1 id="home-heading" className="m-0 text-4xl">
            NPTC Catalogue Maintenance Platform
          </h1>
          <p className="m-0 text-lg text-[var(--color-text-muted)]">
            The National Pathology Test Catalogue: the SPIA Requesting terminology,
            curated by RCPA-QAP, published by NCTS as a SNOMED CT reference set and FHIR
            ValueSet.
          </p>
        </div>
        <div className="max-w-3xl">
          <CatalogueSearchForm />
        </div>
        <div className="flex min-h-10 flex-wrap items-center gap-x-6 gap-y-3">
          <HomeActions />
          <Link to="/catalogue" className={LINK_CLASSES}>
            Search the catalogue
          </Link>
        </div>
      </section>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="flex flex-col gap-3">
          <h2 className="m-0 text-xl">What the catalogue is</h2>
          <p className="m-0 text-[var(--color-text-muted)]">
            The catalogue lists the pathology tests that can be requested.
          </p>
          <Link to="/about" className={LINK_CLASSES}>
            About the catalogue
          </Link>
        </Card>
        <Card className="flex flex-col gap-3">
          <h2 className="m-0 text-xl">Who maintains it</h2>
          <p className="m-0 text-[var(--color-text-muted)]">
            RCPA-QAP curates the catalogue, and NCTS publishes it.
          </p>
        </Card>
        <ContributeCard />
      </div>
    </PageContainer>
  );
}
