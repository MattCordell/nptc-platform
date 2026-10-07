import { Link } from "@tanstack/react-router";

import { Card } from "../components/card.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { ABOUT_INTRO, ABOUT_SECTIONS, ABOUT_TITLE } from "../content/about.ts";

const LINK_CLASSES =
  "inline-flex min-h-6 items-center text-[var(--color-accent)] hover:underline";

export function AboutPage() {
  return (
    <PageContainer className="py-6">
      <PageHeader title={ABOUT_TITLE} meta={ABOUT_INTRO} />
      <div className="flex max-w-3xl flex-col gap-6">
        {ABOUT_SECTIONS.map((section) => {
          const headingId = `about-${section.id}`;
          return (
            <Card
              key={section.id}
              role="region"
              aria-labelledby={headingId}
              className="flex flex-col gap-3"
            >
              <h2 id={headingId} className="m-0 text-xl">
                {section.heading}
              </h2>
              {section.paragraphs.map((text) => (
                <p key={text} className="m-0 text-[var(--color-text-muted)]">
                  {text}
                </p>
              ))}
              {section.links === undefined ? null : (
                <ul className="m-0 flex list-none flex-wrap gap-x-6 gap-y-2 p-0">
                  {section.links.map((link) => (
                    <li key={link.to}>
                      <Link to={link.to} className={LINK_CLASSES}>
                        {link.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          );
        })}
      </div>
    </PageContainer>
  );
}
