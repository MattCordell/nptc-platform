import { Link } from "@tanstack/react-router";

export function HomePage() {
  return (
    <section aria-labelledby="home-heading">
      <h1 id="home-heading">NPTC Catalogue Maintenance Platform</h1>
      <p>
        The National Pathology Test Catalogue: the SPIA Requesting terminology, curated by
        RCPA-QAP, published by NCTS as a SNOMED CT reference set and FHIR ValueSet.
      </p>
      <p>
        <Link to="/catalogue">Search the catalogue</Link>
      </p>
      <p>
        <Link to="/about">About the catalogue</Link>
      </p>
    </section>
  );
}
