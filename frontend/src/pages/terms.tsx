import { useCurrentTerms } from "../api/queries.ts";
import { refusalDetail } from "../api/conflicts.ts";
import { useAuthStatus } from "../auth/auth-status.ts";
import { formatDate } from "../catalogue/format-date.ts";
import { Card } from "../components/card.tsx";
import { Markdown } from "../components/markdown.tsx";
import { NoticePage } from "../components/notice-page.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";

const TITLE = "Terms of use";

/**
 * The public terms-of-use page (NFR-45, NFR-47). Everything on it - the text,
 * the version and the effective date - comes from `GET /auth/terms`; the SPA
 * keeps no copy, so the page always shows what the server will ask a
 * contributor to accept.
 *
 * The h1 is the same in every state so a deep link, a failed load and the
 * loaded page all announce one heading. The text's own headings render one
 * level below it.
 */

export function TermsPage() {
  const terms = useCurrentTerms();
  const status = useAuthStatus();

  if (terms.data === undefined) {
    if (terms.isError) {
      return (
        <NoticePage title={TITLE}>
          <p role="alert" className="m-0 text-[var(--color-danger)]">
            {refusalDetail(terms.error) ??
              "The terms of use could not be loaded. Reload the page to try again."}
          </p>
        </NoticePage>
      );
    }
    return (
      <NoticePage title={TITLE}>
        <p role="status" className="m-0">
          Loading the terms of use…
        </p>
      </NoticePage>
    );
  }

  const { version, effective_date, text, accepted } = terms.data;
  return (
    <PageContainer className="py-6">
      <PageHeader
        title={TITLE}
        meta={
          <>
            Version {version}, effective{" "}
            <time dateTime={effective_date}>
              {formatDate(`${effective_date}T00:00:00`)}
            </time>
          </>
        }
      />
      {status === "signed-in" && accepted ? (
        <p className="m-0">You have accepted this version of the terms.</p>
      ) : null}
      <Card className="max-w-3xl">
        <Markdown text={text} />
      </Card>
    </PageContainer>
  );
}
