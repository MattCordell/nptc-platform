import { useId, type ReactNode } from "react";

import { Card } from "./card.tsx";
import { PageContainer } from "./page-container.tsx";
import { PageHeader } from "./page-header.tsx";
import { useFocusHeadingOnMount } from "./use-focus-heading-on-mount.ts";

type NoticePageProps = {
  title: string;
  /** Lands on the `h1`; generated when omitted. */
  id?: string;
  meta?: ReactNode;
  children: ReactNode;
  /** Links or buttons, rendered after the body. */
  actions?: ReactNode;
  /** Move focus to the `h1` on mount. For screens that replace the requested page. */
  focusHeading?: boolean;
};

/**
 * A screen that says one thing and offers a way on: the stub placeholder,
 * not-found, route error and the sign-in redirect states
 * (docs/architecture/components.md). Composes the layout primitives so those
 * screens look alike; the one `h1` comes from `PageHeader`.
 */
export function NoticePage({
  title,
  id,
  meta,
  children,
  actions,
  focusHeading = false,
}: NoticePageProps) {
  const generatedId = useId();
  const headingId = id ?? generatedId;
  useFocusHeadingOnMount(headingId, focusHeading);

  return (
    <PageContainer className="py-6">
      <PageHeader title={title} meta={meta} id={headingId} />
      <Card className="flex max-w-3xl flex-col gap-4">
        <div className="flex flex-col gap-3 text-[var(--color-text-muted)]">
          {children}
        </div>
        {actions === undefined ? null : (
          <div className="flex flex-wrap items-center gap-3">{actions}</div>
        )}
      </Card>
    </PageContainer>
  );
}
