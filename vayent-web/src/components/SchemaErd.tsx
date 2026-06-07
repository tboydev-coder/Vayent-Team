import React, { useEffect, useMemo, useRef, useState } from "react";

import type { SchemaRelationship, TableMetadata } from "../types";

const CARD_WIDTH = 264;
const CARD_HEIGHT = 178;
const CARD_GAP_X = 36;
const CARD_GAP_Y = 32;

interface SchemaErdProps {
  tables: TableMetadata[];
  relationships: SchemaRelationship[];
  selectedTableName: string;
  onSelectTable: (tableName: string) => void;
}

interface LayoutNode {
  left: number;
  right: number;
  top: number;
  bottom: number;
  centerX: number;
  centerY: number;
}

const buildConnectorPath = (from: LayoutNode, to: LayoutNode): string => {
  const dx = to.centerX - from.centerX;
  const dy = to.centerY - from.centerY;

  let startX = from.centerX;
  let startY = from.centerY;
  let endX = to.centerX;
  let endY = to.centerY;

  if (Math.abs(dx) >= Math.abs(dy)) {
    startX = dx >= 0 ? from.right : from.left;
    startY = from.centerY;
    endX = dx >= 0 ? to.left : to.right;
    endY = to.centerY;

    const midX = (startX + endX) / 2;
    return `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;
  }

  startX = from.centerX;
  startY = dy >= 0 ? from.bottom : from.top;
  endX = to.centerX;
  endY = dy >= 0 ? to.top : to.bottom;

  const midY = (startY + endY) / 2;
  return `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`;
};

const SchemaErd: React.FC<SchemaErdProps> = ({
  tables,
  relationships,
  selectedTableName,
  onSelectTable,
}) => {
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [shellWidth, setShellWidth] = useState(0);

  useEffect(() => {
    const node = shellRef.current;
    if (!node) {
      return;
    }

    const getContentWidth = (element: HTMLDivElement) => {
      const styles = window.getComputedStyle(element);
      const paddingLeft = Number.parseFloat(styles.paddingLeft || "0");
      const paddingRight = Number.parseFloat(styles.paddingRight || "0");

      return element.clientWidth - paddingLeft - paddingRight;
    };

    const syncWidth = (nextWidth: number) => {
      setShellWidth(Math.max(Math.round(nextWidth), 0));
    };

    syncWidth(getContentWidth(node));

    const observer = new ResizeObserver(() => {
      syncWidth(getContentWidth(node));
    });

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    const columnCount =
      tables.length <= 1
        ? 1
        : Math.min(4, Math.max(2, Math.ceil(Math.sqrt(tables.length))));
    const rowCount = Math.max(1, Math.ceil(tables.length / columnCount));
    const naturalWidth =
      columnCount * CARD_WIDTH + (columnCount - 1) * CARD_GAP_X;
    const width = Math.max(shellWidth, naturalWidth, CARD_WIDTH);
    const height = rowCount * CARD_HEIGHT + (rowCount - 1) * CARD_GAP_Y;
    const gapX =
      columnCount > 1
        ? (width - columnCount * CARD_WIDTH) / (columnCount - 1)
        : 0;
    const nodes = new Map<string, LayoutNode>();

    tables.forEach((table, index) => {
      const columnIndex = index % columnCount;
      const rowIndex = Math.floor(index / columnCount);
      const left =
        columnCount === 1
          ? (width - CARD_WIDTH) / 2
          : columnIndex * (CARD_WIDTH + gapX);
      const top = rowIndex * (CARD_HEIGHT + CARD_GAP_Y);

      nodes.set(table.table_name, {
        left,
        right: left + CARD_WIDTH,
        top,
        bottom: top + CARD_HEIGHT,
        centerX: left + CARD_WIDTH / 2,
        centerY: top + CARD_HEIGHT / 2,
      });
    });

    return {
      width: Math.max(width, CARD_WIDTH),
      height: Math.max(height, CARD_HEIGHT),
      nodes,
    };
  }, [shellWidth, tables]);

  const visibleRelationships = useMemo(
    () =>
      relationships.filter(
        (relationship) =>
          relationship.source_table_name !== relationship.target_table_name &&
          layout.nodes.has(relationship.source_table_name) &&
          layout.nodes.has(relationship.target_table_name),
      ),
    [layout.nodes, relationships],
  );

  const relatedTableNames = useMemo(() => {
    if (!selectedTableName) {
      return new Set<string>();
    }

    const names = new Set<string>([selectedTableName]);
    visibleRelationships.forEach((relationship) => {
      if (relationship.source_table_name === selectedTableName) {
        names.add(relationship.target_table_name);
      }
      if (relationship.target_table_name === selectedTableName) {
        names.add(relationship.source_table_name);
      }
    });
    return names;
  }, [selectedTableName, visibleRelationships]);

  return (
    <div ref={shellRef} className="schema-erd-shell">
      <div
        className="schema-erd-stage"
        style={{ width: `${layout.width}px`, height: `${layout.height}px` }}
      >
        <svg
          className="schema-erd-svg"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          preserveAspectRatio="xMinYMin meet"
          aria-hidden="true"
        >
          <defs>
            <marker
              id="schema-erd-arrow"
              markerWidth="10"
              markerHeight="10"
              refX="8"
              refY="5"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="schema-erd-arrowhead" />
            </marker>
          </defs>

          {visibleRelationships.map((relationship) => {
            const from = layout.nodes.get(relationship.source_table_name);
            const to = layout.nodes.get(relationship.target_table_name);

            if (!from || !to) {
              return null;
            }

            const isSelected =
              !selectedTableName ||
              relationship.source_table_name === selectedTableName ||
              relationship.target_table_name === selectedTableName;

            return (
              <path
                key={relationship.id}
                d={buildConnectorPath(from, to)}
                className={`schema-erd-path ${isSelected ? "is-active" : ""}`}
                markerEnd="url(#schema-erd-arrow)"
              />
            );
          })}
        </svg>

        {tables.map((table) => {
          const node = layout.nodes.get(table.table_name);
          if (!node) {
            return null;
          }

          const isSelected = selectedTableName === table.table_name;
          const isRelated =
            !selectedTableName || relatedTableNames.has(table.table_name);
          const relationCount = visibleRelationships.filter(
            (relationship) =>
              relationship.source_table_name === table.table_name ||
              relationship.target_table_name === table.table_name,
          ).length;
          const previewColumns = table.columns.slice(0, 4);

          return (
            <button
              key={table.id}
              type="button"
              className={`schema-erd-node ${isSelected ? "is-selected" : ""} ${
                isRelated ? "is-related" : ""
              }`}
              style={{ left: `${node.left}px`, top: `${node.top}px` }}
              onClick={() => onSelectTable(table.table_name)}
            >
              <div className="schema-erd-node-head">
                <div>
                  <p className="schema-erd-node-title">{table.table_name}</p>
                  <p className="schema-erd-node-meta">
                    {table.columns.length} columns
                  </p>
                </div>
                <span className="schema-erd-node-count">{relationCount}</span>
              </div>

              <div className="schema-erd-node-body">
                {previewColumns.map((column) => (
                  <div key={column.id} className="schema-erd-node-column">
                    <span className="schema-erd-node-column-name">
                      {column.nickname || column.column_name}
                    </span>
                    <span className="schema-erd-node-column-type">
                      {column.data_type}
                    </span>
                  </div>
                ))}

                {table.columns.length > previewColumns.length ? (
                  <p className="schema-erd-node-more">
                    +{table.columns.length - previewColumns.length} more columns
                  </p>
                ) : null}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};

export default SchemaErd;
