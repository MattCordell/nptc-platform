import { Link, useParams } from "@tanstack/react-router";

import { useEntryByCode } from "../api/queries.ts";
import { codeSystemForToken } from "../catalogue/code-systems.ts";
import { CodeLookupResult } from "../catalogue/code-lookup-result.tsx";
import { Breadcrumb } from "../components/breadcrumb.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { CodeChip } from "../components/code-chip.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { useDocumentTitle } from "../shell/use-document-title.ts";

/**
 * The entry that binds one code, found from the address alone (FR-17). Both
 * path segments reach the API as the strings they arrived as (FR-06).
 */

const HEADING_ID = "code-by-token-heading";

export function CodeByTokenPage() {
  const { systemToken, code } = useParams({ from: "/catalogue/code/$systemToken/$code" });
  const query = useEntryByCode(systemToken, code);
  useDocumentTitle("Code lookup — NPTC Catalogue");
  const system = codeSystemForToken(systemToken);

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
          meta={
            <span className="flex flex-wrap items-center gap-2">
              <span>{system?.label ?? systemToken}</span>
              <CodeChip code={code} />
            </span>
          }
        />
        <CodeLookupResult query={query} code={code} />
        <div>
          <Link to="/catalogue/lookup" className={buttonClassName("secondary")}>
            Look up another code
          </Link>
        </div>
      </PageContainer>
    </section>
  );
}
