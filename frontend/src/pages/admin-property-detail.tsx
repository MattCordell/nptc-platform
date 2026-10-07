import { Link, useParams } from "@tanstack/react-router";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { usePropertyDefinition } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import { ApiError } from "../api/unwrap.ts";
import {
  bindingStrengthLabelFor,
  bindingTargetLabelFor,
  cardinalityLabelFor,
  constraintValueText,
  originLabelFor,
  scopeLabelFor,
} from "../catalogue/property-display.ts";
import { statusLabelFor, statusToneFor } from "../catalogue/status-options.ts";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";

/**
 * The property registry detail screen (FR-08..13, NFR-31): one property's
 * whole definition, read-only.
 *
 * It shows what the API returns and nothing datatype-specific. `datatype` is
 * plain text, the binding rows appear when their value is present, and
 * `constraints` is listed key by key without interpreting any key, so a new
 * datatype needs no change here (FR-77, ADR-0013).
 */

type Definition = components["schemas"]["PropertyDefinitionResponse"];

const LABEL_CLASS = "text-[var(--color-text-muted)]";

function staleWarning(key: string): string {
  return (
    `${key} could not be refreshed just now, so what follows may be out of date. ` +
    "Reload the page to try again."
  );
}

function loadFailureMessage(key: string, error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return `No property was found for ${key}. Check the key.`;
  }
  return (
    refusalDetail(error) ??
    `${key} could not be loaded. Try again, or contact an administrator if the problem persists.`
  );
}

function yesNo(value: boolean): string {
  return value ? "Yes" : "No";
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className={LABEL_CLASS}>{term}</dt>
      <dd className="m-0">{children}</dd>
    </>
  );
}

function DefinitionList({ children }: { children: ReactNode }) {
  return (
    <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-3">{children}</dl>
  );
}

function DefinitionCard({ definition }: { definition: Definition }) {
  return (
    <Card>
      <h2 className="m-0 mb-4 text-xl">Definition</h2>
      <DefinitionList>
        <Row term="Key">
          <span className="font-mono">{definition.key}</span>
        </Row>
        <Row term="Label">{definition.label}</Row>
        <Row term="Datatype">
          <span className="font-mono">{definition.datatype}</span>
        </Row>
        <Row term="Cardinality">{cardinalityLabelFor(definition.cardinality)}</Row>
        <Row term="Scope">{scopeLabelFor(definition.scope)}</Row>
        <Row term="Status">
          <StatusBadge
            tone={statusToneFor(definition.status)}
            label={statusLabelFor(definition.status)}
          />
        </Row>
        <Row term="Origin">{originLabelFor(definition.origin)}</Row>
        <Row term="Required for submission">
          {yesNo(definition.required_for_submission)}
        </Row>
        <Row term="Required for publication">
          {yesNo(definition.required_for_publication)}
        </Row>
        <Row term="Used as a catalogue filter">{yesNo(definition.filterable)}</Row>
        <Row term="Display order">
          <span className="tabular-nums">{definition.display_order}</span>
        </Row>
        <Row term="Data-entry control">
          <span className="font-mono">{definition.form_control.control}</span>
        </Row>
      </DefinitionList>
    </Card>
  );
}

function BindingCard({ definition }: { definition: Definition }) {
  return (
    <Card>
      <h2 className="m-0 mb-4 text-xl">Terminology binding</h2>
      <DefinitionList>
        {definition.binding_target !== null && (
          <Row term="Bound to">{bindingTargetLabelFor(definition.binding_target)}</Row>
        )}
        {definition.value_set_uri !== null && (
          <Row term="Value set">
            <span className="font-mono break-all">{definition.value_set_uri}</span>
          </Row>
        )}
        {definition.local_code_system_key !== null && (
          <Row term="Local code system">
            <span className="font-mono">{definition.local_code_system_key}</span>
          </Row>
        )}
        {definition.strength !== null && (
          <Row term="Binding strength">
            {bindingStrengthLabelFor(definition.strength)}
          </Row>
        )}
        {definition.edition !== null && (
          <Row term="Edition">
            <span className="font-mono">{definition.edition}</span>
          </Row>
        )}
      </DefinitionList>
    </Card>
  );
}

function ConstraintsCard({ constraints }: { constraints: Record<string, unknown> }) {
  return (
    <Card>
      <h2 className="m-0 mb-4 text-xl">Constraints</h2>
      <DefinitionList>
        {Object.entries(constraints).map(([name, value]) => (
          <Row key={name} term={name}>
            <span className="font-mono break-all">{constraintValueText(value)}</span>
          </Row>
        ))}
      </DefinitionList>
    </Card>
  );
}

function hasBinding(definition: Definition): boolean {
  return (
    definition.binding_target !== null ||
    definition.value_set_uri !== null ||
    definition.local_code_system_key !== null ||
    definition.strength !== null ||
    definition.edition !== null
  );
}

export function AdminPropertyDetailPage() {
  const { propertyKey } = useParams({
    from: "/authenticated/admin/properties/$propertyKey",
  });
  const property = usePropertyDefinition(propertyKey);
  const { message, politeness, announce } = useAnnounce();

  const staleData = property.isError && property.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(staleWarning(propertyKey));
    }
  }, [staleData, propertyKey, announce]);

  // The message, not the error, is the dependency: a refetch that fails the
  // same way yields a new error object with unchanged wording, which would
  // otherwise be announced twice.
  const hardFailureMessage =
    property.isError && property.data === undefined
      ? loadFailureMessage(propertyKey, property.error)
      : null;
  useEffect(() => {
    if (hardFailureMessage !== null) {
      announce(hardFailureMessage);
    }
  }, [hardFailureMessage, announce]);

  const definition = property.data;

  return (
    <section aria-labelledby="property-detail-heading">
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id="property-detail-heading"
          title={definition ? definition.label : propertyKey}
          meta={
            definition ? <span className="font-mono">{definition.key}</span> : undefined
          }
          actions={
            <Link to="/admin/properties" className={buttonClassName("secondary")}>
              Back to the property registry
            </Link>
          }
        />

        {property.isPending && <p>Loading {propertyKey}…</p>}

        {hardFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p>{staleWarning(propertyKey)}</p>}

        {definition && (
          <>
            <DefinitionCard definition={definition} />
            {hasBinding(definition) && <BindingCard definition={definition} />}
            {Object.keys(definition.constraints).length > 0 && (
              <ConstraintsCard constraints={definition.constraints} />
            )}
          </>
        )}
      </PageContainer>
    </section>
  );
}
