import { Link } from "@tanstack/react-router";

export function SiteFooter() {
  return (
    <footer>
      <nav aria-label="Footer">
        <ul>
          <li>
            <Link to="/about">About the catalogue</Link>
          </li>
          <li>
            <Link to="/exports">Exports</Link>
          </li>
          <li>
            <Link to="/terms">Terms of use</Link>
          </li>
        </ul>
      </nav>
      <p>
        RCPA-QAP National Pathology Test Catalogue &mdash; published by NCTS as a SNOMED
        CT reference set and FHIR ValueSet.
      </p>
    </footer>
  );
}
