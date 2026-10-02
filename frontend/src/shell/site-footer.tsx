import { Link } from "@tanstack/react-router";

const FOOTER_LINK_CLASSES =
  "inline-block rounded-control px-1 py-1 text-sm text-[var(--color-accent)] hover:underline";

export function SiteFooter() {
  return (
    <footer className="mt-8 border-t border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="max-w-page mx-auto flex flex-col gap-3 px-6 py-6">
        <nav aria-label="Footer">
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
            <li>
              <Link to="/about" className={FOOTER_LINK_CLASSES}>
                About the catalogue
              </Link>
            </li>
            <li>
              <Link to="/exports" className={FOOTER_LINK_CLASSES}>
                Exports
              </Link>
            </li>
            <li>
              <Link to="/terms" className={FOOTER_LINK_CLASSES}>
                Terms of use
              </Link>
            </li>
          </ul>
        </nav>
        <p className="m-0 text-sm text-[var(--color-text-muted)]">
          RCPA-QAP National Pathology Test Catalogue &mdash; published by NCTS as a SNOMED
          CT reference set and FHIR ValueSet.
        </p>
      </div>
    </footer>
  );
}
