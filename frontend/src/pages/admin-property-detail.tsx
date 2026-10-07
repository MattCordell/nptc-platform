import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { usePropertyDefinition } from "../api/queries.ts";
import type { components } from "../api/schema.ts";
import {
  bindingStrengthLabelFor,
  bindingTargetLabelFor,
  cardinalityLabelFor,
  constraintValueText,
  originLabelFor,
  scopeLabelFor,
} from "../catalogue/property-display.ts";
import { statusLabelFor, statusToneFor } from "../catalogue/status-options.ts";
import { Button } from "../components/button.tsx";
import { buttonClassName } from "../components/button-class-name.ts";
import { Card } from "../components/card.tsx";
import { LiveRegion } from "../components/live-region.tsx";
import { PageContainer } from "../components/page-container.tsx";
import { PageHeader } from "../components/page-header.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { useAnnounce } from "../components/use-announce.ts";
import { DeprecatePropertyDialog } from "../registry/deprecate-property-dialog.tsx";
import {
  propertyLoadFailureMessage,
  propertyStaleWarning,
} from "../registry/property-load.ts";

/**
 * The property registry detail screen (FR-08..13, NFR-31): one property's
 * whole definition, with the actions that change it (Edit and Deprecate).
 *
 * It shows what the API returns and nothing datatype-specific. `datatype` is
 * plain text, the binding rows appear when their value is present, and
 * `constraints` is listed key by key without interpreting any key, so a new
 * datatype needs no change here (FR-77, ADR-0013).
 */

type Definition = components["schemas"]["PropertyDefinitionResponse"];

const HEADING_ID = "property-detail-heading";

const LABEL_CLASS = "text-[var(--color-text-muted)]";

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
  const [deprecating, setDeprecating] = useState(false);
  const awaitingDeprecation = useRef(false);

  const staleData = property.isError && property.data !== undefined;
  useEffect(() => {
    if (staleData) {
      announce(propertyStaleWarning(propertyKey));
    }
  }, [staleData, propertyKey, announce]);

  // The message, not the error, is the dependency: a refetch that fails the
  // same way yields a new error object with unchanged wording, which would
  // otherwise be announced twice.
  const hardFailureMessage =
    property.isError && property.data === undefined
      ? propertyLoadFailureMessage(propertyKey, property.error)
      : null;
  useEffect(() => {
    if (hardFailureMessage !== null) {
      announce(hardFailureMessage);
    }
  }, [hardFailureMessage, announce]);

  const definition = property.data;
  const isDeprecated = definition?.status === "deprecated";

  // The Deprecate button disappears once the refetch shows the new status, and
  // the dialog would restore focus to it. So focus moves to the heading when
  // that status arrives, and the change is announced.
  useEffect(() => {
    if (awaitingDeprecation.current && definition !== undefined && isDeprecated) {
      awaitingDeprecation.current = false;
      announce(`${definition.label} is now deprecated.`);
      document.getElementById(HEADING_ID)?.focus();
    }
  }, [definition, isDeprecated, announce]);

  return (
    <section aria-labelledby={HEADING_ID}>
      <LiveRegion message={message} politeness={politeness} />

      <PageContainer className="py-6">
        <PageHeader
          id={HEADING_ID}
          focusable
          title={definition ? definition.label : propertyKey}
          meta={
            definition ? <span className="font-mono">{definition.key}</span> : undefined
          }
          actions={
            <>
              {definition && (
                <Link
                  to="/admin/properties/$propertyKey/edit"
                  params={{ propertyKey }}
                  className={buttonClassName("primary")}
                >
                  Edit property
                </Link>
              )}
              {definition && !isDeprecated && (
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => setDeprecating(true)}
                >
                  Deprecate property
                </Button>
              )}
              <Link to="/admin/properties" className={buttonClassName("secondary")}>
                Back to the property registry
              </Link>
            </>
          }
        />

        {property.isPending && <p>Loading {propertyKey}…</p>}

        {hardFailureMessage !== null && (
          <p className="m-0 text-[var(--color-danger)]">{hardFailureMessage}</p>
        )}

        {staleData && <p>{propertyStaleWarning(propertyKey)}</p>}

        {definition && (
          <>
            <DefinitionCard definition={definition} />
            {hasBinding(definition) && <BindingCard definition={definition} />}
            {Object.keys(definition.constraints).length > 0 && (
              <ConstraintsCard constraints={definition.constraints} />
            )}
          </>
        )}

        {deprecating && definition && (
          <DeprecatePropertyDialog
            definition={definition}
            onClose={() => setDeprecating(false)}
            onDeprecated={() => {
              awaitingDeprecation.current = true;
              setDeprecating(false);
            }}
          />
        )}
      </PageContainer>
    </section>
  );
}
