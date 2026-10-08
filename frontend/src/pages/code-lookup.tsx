import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";

import { useEntryBySystemCode } from "../api/queries.ts";
import {
  CODE_SYSTEMS,
  DEFAULT_CODE_SYSTEM,
  codeSystemForUri,
} from "../catalogue/code-systems.ts";
import { CodeLookupResult } from "../catalogue/code-lookup-result.tsx";
import { Breadcrumb } from "../components/breadcrumb.tsx";
import { Card } from "../components/card.tsx";
import { Field } from "../components/field.tsx";
import { Form } from "../components/form.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { Select } from "../components/select.tsx";
import { useDocumentTitle } from "../shell/use-document-title.ts";

/**
 * Find the catalogue entry that binds a code (FR-17). The form sends the user
 * to the direct code route. An address that already carries a system URI and a
 * code, `/catalogue/lookup?system=...&code=...`, is looked up here, under the
 * form. The code stays a string from the input to the address (FR-06).
 */

const HEADING_ID = "code-lookup-heading";
const SYSTEM_FIELD_ID = "code-lookup-system";
const CODE_FIELD_ID = "code-lookup-code";

const SYSTEM_OPTIONS = CODE_SYSTEMS.map((system) => ({
  value: system.token,
  label: system.label,
}));

/**
 * Owns what the user has typed. The page keys it on the address, so a move to
 * another `?system=&code=` on this route starts it from that address instead of
 * keeping the earlier text.
 */
function LookupForm({
  initialToken,
  initialCode,
}: {
  initialToken: string;
  initialCode: string;
}) {
  const navigate = useNavigate();
  const [token, setToken] = useState(initialToken);
  const [code, setCode] = useState(initialCode);
  const [attempted, setAttempted] = useState(false);

  const trimmedCode = code.trim();
  const errors =
    attempted && trimmedCode === ""
      ? [{ fieldId: CODE_FIELD_ID, message: "Enter a code." }]
      : [];

  function handleSubmit() {
    setAttempted(true);
    if (trimmedCode === "") {
      return;
    }
    void navigate({
      to: "/catalogue/code/$systemToken/$code",
      params: { systemToken: token, code: trimmedCode },
    });
  }

  return (
    <Card className="max-w-3xl">
      <Form
        aria-label="Look up a code"
        onSubmit={handleSubmit}
        errors={errors}
        errorSummaryTitle="Enter a code to look up"
        submitLabel="Look up"
      >
        <Select
          id={SYSTEM_FIELD_ID}
          label="Code system"
          options={SYSTEM_OPTIONS}
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
        <Field
          id={CODE_FIELD_ID}
          label="Code"
          hint="Type the code exactly as written, including any leading zeros."
          error={errors[0]?.message}
        >
          {(control) => (
            <input
              {...control}
              type="text"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              className={INPUT_CLASSES}
            />
          )}
        </Field>
      </Form>
    </Card>
  );
}

export function CodeLookupPage() {
  const search = useSearch({ from: "/catalogue/lookup" });
  useDocumentTitle("Code lookup — NPTC Catalogue");

  const addressedSystem = search.system.trim();
  const addressedCode = search.code.trim();
  const addressed = useEntryBySystemCode(addressedSystem, addressedCode);
  const hasAddressedLookup = addressedSystem !== "" && addressedCode !== "";

  return (
    <section aria-labelledby={HEADING_ID}>
      <PageContainer className="flex flex-col gap-4 py-6">
        <Breadcrumb
          ancestors={[
            <Link key="home" to="/">
              Home
            </Link>,
            <Link key="catalogue" to="/catalogue">
              Catalogue
            </Link>,
          ]}
          current="Code lookup"
        />
        <PageHeader
          id={HEADING_ID}
          title="Code lookup"
          meta="Find the catalogue entry that binds a code."
        />
        <LookupForm
          key={`form|${search.system}|${search.code}`}
          initialToken={
            codeSystemForUri(search.system)?.token ?? DEFAULT_CODE_SYSTEM.token
          }
          initialCode={search.code}
        />
        {hasAddressedLookup ? (
          <CodeLookupResult
            key={`result|${addressedSystem}|${addressedCode}`}
            query={addressed}
            code={addressedCode}
          />
        ) : null}
      </PageContainer>
    </section>
  );
}
