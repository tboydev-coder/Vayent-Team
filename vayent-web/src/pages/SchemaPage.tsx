import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import SchemaErd from "../components/SchemaErd";
import api from "../services/api";
import "../styles/schema.css";
import type { ColumnMetadata, DatabaseSchema, SchemaRelationship } from "../types";

type AnnotationTarget = "schema" | "table" | "column";

interface AnnotationMutationInput {
  payload: {
    target_type: AnnotationTarget;
    table_name?: string;
    column_name?: string;
    nickname?: string | null;
    description?: string | null;
  };
  scopeKey: string;
  successMessage: string;
}

const normalizeText = (value: string): string | null => {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const areEqualText = (left: string | null, right: string | null): boolean =>
  (left ?? "") === (right ?? "");

const getConstraintText = (column: ColumnMetadata): string =>
  [
    column.is_primary_key ? "PK" : null,
    column.is_foreign_key ? `FK ${column.foreign_key_reference}` : null,
    column.is_nullable ? "nullable" : "required",
  ]
    .filter(Boolean)
    .join(" - ");

const getRelationshipLabel = (
  relationship: SchemaRelationship,
): string =>
  `${relationship.source_table_name}.${relationship.source_column_name} -> ${relationship.target_table_name}.${relationship.target_column_name}`;

const SchemaPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [selectedTableName, setSelectedTableName] = useState("");
  const [selectedColumnName, setSelectedColumnName] = useState("");
  const [schemaDescriptionDraft, setSchemaDescriptionDraft] = useState("");
  const [tableDescriptionDraft, setTableDescriptionDraft] = useState("");
  const [columnNicknameDraft, setColumnNicknameDraft] = useState("");
  const [columnDescriptionDraft, setColumnDescriptionDraft] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [feedbackTone, setFeedbackTone] = useState<"success" | "error">("success");

  const { data, isLoading } = useQuery<DatabaseSchema>({
    queryKey: ["schema", id],
    enabled: Boolean(id),
    queryFn: async () => {
      const res = await api.get(`/connections/${id}/schema`);
      return res.data as DatabaseSchema;
    },
  });

  const annotationMutation = useMutation<DatabaseSchema, Error, AnnotationMutationInput>({
    mutationFn: async ({ payload }) => {
      const res = await api.put(`/connections/${id}/schema/annotations`, payload);
      return res.data as DatabaseSchema;
    },
    onSuccess: (nextSchema, variables) => {
      if (id) {
        queryClient.setQueryData(["schema", id], nextSchema);
      }
      setFeedback(variables.successMessage);
      setFeedbackTone("success");
    },
    onError: (error) => {
      setFeedback(error.message);
      setFeedbackTone("error");
    },
  });

  const selectedTable = useMemo(
    () =>
      data?.tables.find((table) => table.table_name === selectedTableName) ?? null,
    [data?.tables, selectedTableName],
  );

  const selectedColumn = useMemo(
    () =>
      selectedTable?.columns.find((column) => column.column_name === selectedColumnName) ??
      null,
    [selectedColumnName, selectedTable?.columns],
  );

  const selectedTableRelationships = useMemo(
    () =>
      selectedTable
        ? data?.relationships.filter(
            (relationship) =>
              relationship.source_table_name === selectedTable.table_name ||
              relationship.target_table_name === selectedTable.table_name,
          ) ?? []
        : [],
    [data?.relationships, selectedTable],
  );

  const relationshipCounts = useMemo(() => {
    if (!selectedTable) {
      return { incoming: 0, outgoing: 0 };
    }

    return {
      incoming: selectedTableRelationships.filter(
        (relationship) => relationship.target_table_name === selectedTable.table_name,
      ).length,
      outgoing: selectedTableRelationships.filter(
        (relationship) => relationship.source_table_name === selectedTable.table_name,
      ).length,
    };
  }, [selectedTable, selectedTableRelationships]);

  const annotationCount = useMemo(() => {
    if (!data) {
      return 0;
    }

    let count = data.schema_description ? 1 : 0;

    data.tables.forEach((table) => {
      if (table.table_description) {
        count += 1;
      }

      table.columns.forEach((column) => {
        if (column.nickname || column.column_description) {
          count += 1;
        }
      });
    });

    return count;
  }, [data]);

  useEffect(() => {
    if (!data?.tables.length) {
      setSelectedTableName("");
      return;
    }

    const hasSelectedTable = data.tables.some(
      (table) => table.table_name === selectedTableName,
    );
    if (!hasSelectedTable) {
      setSelectedTableName(data.tables[0].table_name);
    }
  }, [data?.tables, selectedTableName]);

  useEffect(() => {
    setSchemaDescriptionDraft(data?.schema_description ?? "");
  }, [data?.schema_description]);

  useEffect(() => {
    setTableDescriptionDraft(selectedTable?.table_description ?? "");
  }, [selectedTable?.table_description]);

  useEffect(() => {
    if (!selectedTable?.columns.length) {
      setSelectedColumnName("");
      return;
    }

    const hasSelectedColumn = selectedTable.columns.some(
      (column) => column.column_name === selectedColumnName,
    );
    if (!hasSelectedColumn) {
      setSelectedColumnName(selectedTable.columns[0].column_name);
    }
  }, [selectedColumnName, selectedTable?.columns]);

  useEffect(() => {
    setColumnNicknameDraft(selectedColumn?.nickname ?? "");
    setColumnDescriptionDraft(selectedColumn?.column_description ?? "");
  }, [selectedColumn?.nickname, selectedColumn?.column_description]);

  const schemaDescriptionChanged = !areEqualText(
    normalizeText(schemaDescriptionDraft),
    data?.schema_description ?? null,
  );

  const tableDescriptionChanged = !areEqualText(
    normalizeText(tableDescriptionDraft),
    selectedTable?.table_description ?? null,
  );

  const columnAnnotationChanged =
    !areEqualText(normalizeText(columnNicknameDraft), selectedColumn?.nickname ?? null) ||
    !areEqualText(
      normalizeText(columnDescriptionDraft),
      selectedColumn?.column_description ?? null,
    );

  const pendingScope = annotationMutation.isPending
    ? annotationMutation.variables?.scopeKey
    : null;

  const handleSelectTable = (tableName: string) => {
    setSelectedTableName(tableName);
    setSelectedColumnName("");
  };

  const saveSchemaDescription = () => {
    annotationMutation.mutate({
      scopeKey: "schema",
      successMessage: normalizeText(schemaDescriptionDraft)
        ? "Schema notes saved."
        : "Schema notes cleared.",
      payload: {
        target_type: "schema",
        description: normalizeText(schemaDescriptionDraft),
      },
    });
  };

  const saveTableDescription = () => {
    if (!selectedTable) {
      return;
    }

    annotationMutation.mutate({
      scopeKey: `table:${selectedTable.table_name}`,
      successMessage: normalizeText(tableDescriptionDraft)
        ? `Notes saved for ${selectedTable.table_name}.`
        : `Notes cleared for ${selectedTable.table_name}.`,
      payload: {
        target_type: "table",
        table_name: selectedTable.table_name,
        description: normalizeText(tableDescriptionDraft),
      },
    });
  };

  const saveColumnAnnotation = () => {
    if (!selectedTable || !selectedColumn) {
      return;
    }

    annotationMutation.mutate({
      scopeKey: `column:${selectedTable.table_name}.${selectedColumn.column_name}`,
      successMessage:
        normalizeText(columnNicknameDraft) || normalizeText(columnDescriptionDraft)
          ? `Context saved for ${selectedColumn.column_name}.`
          : `Context cleared for ${selectedColumn.column_name}.`,
      payload: {
        target_type: "column",
        table_name: selectedTable.table_name,
        column_name: selectedColumn.column_name,
        nickname: normalizeText(columnNicknameDraft),
        description: normalizeText(columnDescriptionDraft),
      },
    });
  };

  if (isLoading) {
    return (
      <div className="app-page">
        <div className="app-empty">Loading schema...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="app-page">
        <div className="app-empty">Schema could not be loaded for this connection.</div>
      </div>
    );
  }

  return (
    <div className="app-page schema-layout">
      <section className="dashboard-hero">
        <div className="schema-hero">
          <div className="schema-copy">
            <p className="page-kicker">Schema Explorer</p>
            <h1 className="display-title">{data.schema_name}</h1>
            <p className="page-text">
              Add human-friendly meaning on top of synced database metadata, then use
              the live relationship map to move through the model faster.
            </p>
          </div>

          <div className="schema-actions">
            <div className="brand-pill">{data.tables.length} tables</div>
            <div className="brand-pill">{data.relationships.length} relationships</div>
            <div className="brand-pill">{annotationCount} annotations</div>
            <Link to={`/chat?connectionId=${id}`} className="brand-btn-primary">
              Start chat
            </Link>
          </div>
        </div>
      </section>

      <div className="schema-overview-grid">
        <section className="app-panel schema-notes-panel">
          <div className="schema-card-head">
            <div>
              <p className="page-kicker">Semantic Notes</p>
              <h2 className="schema-card-title">Describe the database in your own words</h2>
            </div>
            <div className="brand-badge">AI context</div>
          </div>

          <p className="schema-card-copy">
            These notes stay inside Vayent, never touch the source database, and are
            injected into the schema context used for query generation.
          </p>

          <textarea
            className="input schema-textarea"
            rows={5}
            placeholder="Example: Revenue lives in orders, trials are tracked in subscriptions, and churn means a canceled subscription with no reactivation."
            value={schemaDescriptionDraft}
            onChange={(event) => setSchemaDescriptionDraft(event.target.value)}
          />

          <div className="schema-actions-row">
            <button
              type="button"
              className="brand-btn-secondary"
              onClick={saveSchemaDescription}
              disabled={!schemaDescriptionChanged || pendingScope === "schema"}
            >
              {pendingScope === "schema" ? "Saving..." : "Save schema notes"}
            </button>
            <p className="schema-inline-help">
              Works well for business definitions, naming quirks, and table intent.
            </p>
          </div>
        </section>

        <section className="app-panel-strong schema-erd-panel">
          <div className="schema-card-head">
            <div>
              <p className="page-kicker">Visual ERD</p>
              <h2 className="schema-card-title">Live relationship map</h2>
            </div>
            <div className="brand-badge">Selected database</div>
          </div>

          <p className="schema-card-copy">
            Click any table card to jump the browser and annotation panels to that part
            of the schema.
          </p>

          <SchemaErd
            tables={data.tables}
            relationships={data.relationships}
            selectedTableName={selectedTableName}
            onSelectTable={handleSelectTable}
          />
        </section>
      </div>

      {data.tables.length === 0 ? (
        <div className="app-empty schema-empty">
          <p>No tables were found in this schema.</p>
        </div>
      ) : (
        <section className="app-panel-strong schema-selector-panel">
          <div className="schema-selector-head">
            <div>
              <p className="page-kicker">Table Browser</p>
              <h2 className="schema-selector-title">Inspect tables and teach the model</h2>
            </div>
            <div className="glass-select-wrap schema-select-wrap">
              <select
                id="schema-table-select"
                className="input glass-select"
                value={selectedTableName}
                onChange={(event) => handleSelectTable(event.target.value)}
              >
                {data.tables.map((table) => (
                  <option key={table.id} value={table.table_name}>
                    {table.table_name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {feedback ? (
            <p
              className={
                feedbackTone === "success"
                  ? "schema-feedback schema-feedback-success"
                  : "schema-feedback schema-feedback-error"
              }
            >
              {feedback}
            </p>
          ) : null}

          {selectedTable ? (
            <div className="schema-detail-grid">
              <div className="schema-detail-main">
                <section className="app-panel schema-table-panel">
                  <div className="schema-section-head">
                    <div>
                      <p className="schema-section-title">{selectedTable.table_name}</p>
                      <p className="schema-section-meta">
                        {selectedTable.columns.length} columns
                        {selectedTable.row_count !== null
                          ? ` - ~${selectedTable.row_count} rows`
                          : ""}
                      </p>
                    </div>
                    <div className="schema-badge-stack">
                      <div className="brand-badge">
                        {relationshipCounts.outgoing} outgoing
                      </div>
                      <div className="brand-badge">
                        {relationshipCounts.incoming} incoming
                      </div>
                    </div>
                  </div>

                  <div className="schema-table-notes">
                    <label className="schema-field-label" htmlFor="schema-table-description">
                      Table description
                    </label>
                    <textarea
                      id="schema-table-description"
                      className="input schema-textarea"
                      rows={4}
                      placeholder="Explain what this table represents and how analysts should think about it."
                      value={tableDescriptionDraft}
                      onChange={(event) => setTableDescriptionDraft(event.target.value)}
                    />
                    <div className="schema-actions-row">
                      <button
                        type="button"
                        className="brand-btn-secondary"
                        onClick={saveTableDescription}
                        disabled={
                          !tableDescriptionChanged ||
                          pendingScope === `table:${selectedTable.table_name}`
                        }
                      >
                        {pendingScope === `table:${selectedTable.table_name}`
                          ? "Saving..."
                          : "Save table notes"}
                      </button>
                      <p className="schema-inline-help">
                        Good for table purpose, grain, freshness, or caveats.
                      </p>
                    </div>
                  </div>

                  <div className="schema-relations">
                    <div className="schema-relations-head">
                      <p className="schema-subtitle">Relationships touching this table</p>
                      <span className="brand-pill">{selectedTableRelationships.length}</span>
                    </div>

                    {selectedTableRelationships.length > 0 ? (
                      <div className="schema-relations-list">
                        {selectedTableRelationships.map((relationship) => {
                          const nextTableName =
                            relationship.source_table_name === selectedTable.table_name
                              ? relationship.target_table_name
                              : relationship.source_table_name;

                          return (
                            <button
                              key={relationship.id}
                              type="button"
                              className="schema-relation-pill"
                              onClick={() => handleSelectTable(nextTableName)}
                            >
                              {getRelationshipLabel(relationship)}
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="schema-inline-help">
                        No foreign-key relationships were detected for this table.
                      </p>
                    )}
                  </div>
                </section>

                <section className="app-panel schema-columns-panel">
                  <div className="schema-section-head">
                    <div>
                      <p className="schema-subtitle">Columns</p>
                      <p className="schema-section-meta">
                        Pick a column to add a nickname or description.
                      </p>
                    </div>
                    <div className="brand-badge">Semantic aliases</div>
                  </div>

                  <div className="schema-table-wrap app-scroll-x">
                    <table className="schema-table">
                      <thead>
                        <tr>
                          <th>Column</th>
                          <th>Nickname</th>
                          <th>Type</th>
                          <th>Constraints</th>
                          <th>Notes</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedTable.columns.map((column) => {
                          const isSelected = selectedColumnName === column.column_name;

                          return (
                            <tr
                              key={column.id}
                              className={isSelected ? "is-selected" : undefined}
                            >
                              <td className="schema-col-main">
                                <button
                                  type="button"
                                  className="schema-column-trigger"
                                  onClick={() => setSelectedColumnName(column.column_name)}
                                >
                                  <span>{column.column_name}</span>
                                  {column.is_primary_key ? (
                                    <span className="schema-inline-badge">PK</span>
                                  ) : null}
                                </button>
                              </td>
                              <td className="schema-col-type">
                                {column.nickname || "None"}
                              </td>
                              <td className="schema-col-type">{column.data_type}</td>
                              <td className="schema-col-meta">
                                {getConstraintText(column)}
                              </td>
                              <td className="schema-col-note">
                                {column.column_description || "No notes yet"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              </div>

              <aside className="app-panel schema-column-panel">
                <div className="schema-card-head">
                  <div>
                    <p className="page-kicker">Column Annotation</p>
                    <h2 className="schema-card-title">
                      {selectedColumn ? selectedColumn.column_name : "Choose a column"}
                    </h2>
                  </div>
                  {selectedColumn?.nickname ? (
                    <div className="brand-badge">{selectedColumn.nickname}</div>
                  ) : null}
                </div>

                {selectedColumn ? (
                  <>
                    <p className="schema-card-copy">
                      Use a nickname when the real column name is cryptic. Add a
                      description when the meaning, unit, or business rule is easy to
                      miss.
                    </p>

                    <div className="schema-field-group">
                      <label
                        className="schema-field-label"
                        htmlFor="schema-column-nickname"
                      >
                        Nickname
                      </label>
                      <input
                        id="schema-column-nickname"
                        className="input"
                        placeholder="Example: Customer tier"
                        value={columnNicknameDraft}
                        onChange={(event) => setColumnNicknameDraft(event.target.value)}
                      />
                    </div>

                    <div className="schema-field-group">
                      <label
                        className="schema-field-label"
                        htmlFor="schema-column-description"
                      >
                        Description
                      </label>
                      <textarea
                        id="schema-column-description"
                        className="input schema-textarea"
                        rows={6}
                        placeholder="Example: Plan bucket used for billing analytics. Values are free, pro, and enterprise."
                        value={columnDescriptionDraft}
                        onChange={(event) =>
                          setColumnDescriptionDraft(event.target.value)
                        }
                      />
                    </div>

                    <div className="schema-actions-row schema-actions-row-vertical">
                      <button
                        type="button"
                        className="brand-btn-primary"
                        onClick={saveColumnAnnotation}
                        disabled={
                          !columnAnnotationChanged ||
                          pendingScope ===
                            `column:${selectedTable.table_name}.${selectedColumn.column_name}`
                        }
                      >
                        {pendingScope ===
                        `column:${selectedTable.table_name}.${selectedColumn.column_name}`
                          ? "Saving..."
                          : "Save column context"}
                      </button>
                      <p className="schema-inline-help">
                        Stored separately from the source schema and available to the AI
                        query planner immediately.
                      </p>
                    </div>
                  </>
                ) : (
                  <div className="app-empty schema-selector-empty">
                    <p>Select a column to reveal its annotation editor.</p>
                  </div>
                )}
              </aside>
            </div>
          ) : (
            <div className="app-empty schema-selector-empty">
              <p>Select a table to reveal its schema.</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
};

export default SchemaPage;
