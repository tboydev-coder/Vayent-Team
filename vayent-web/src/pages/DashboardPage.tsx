import React, { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../services/api";
import { useAuthStore } from "../store/auth";
import {
  getActiveConnectionId,
  setActiveConnectionId,
} from "../utils/activeConnection";
import "../styles/dashboard.css";
import type {
  ConnectedSource,
  ConnectedSourceList,
  CopilotArtifact,
  CopilotArtifactList,
  DatabaseSchema,
  SyncSourceResponse,
} from "../types";

type ChartKind =
  | "kpi"
  | "bar"
  | "line"
  | "pie"
  | "donut"
  | "area"
  | "distribution"
  | "heatmap"
  | "funnel"
  | "cohort"
  | "forecast"
  | "table";
type DashboardMode = "create" | "modify";

interface DashboardCard {
  title?: unknown;
  description?: unknown;
  sql?: unknown;
  status?: unknown;
  value?: unknown;
  row_count?: unknown;
  rows?: unknown;
  error?: unknown;
  visualization?: unknown;
  chart_type?: unknown;
  explanation?: unknown;
  interpretation?: unknown;
  recommended_action?: unknown;
  source_name?: unknown;
}

interface InsightItem {
  title: string;
  body: string;
  tone: "positive" | "warning" | "neutral";
}

interface RecommendationItem {
  title: string;
  body: string;
  priority: "High" | "Medium" | "Low";
}

interface SeriesPoint {
  label: string;
  value: number;
}

const chartPalette = [
  "#4f46e5",
  "#4338ca",
  "#6366f1",
  "#818cf8",
  "#a5b4fc",
  "#c7d2fe",
];

const quickPrompts = [
  "Create a sales performance dashboard.",
  "Show user growth trends.",
  "Build a dashboard for customer retention.",
  "Generate financial insights.",
  "Create an executive summary dashboard.",
];

const dateRanges = [
  "Last 7 days",
  "Last 30 days",
  "Quarter to date",
  "Year to date",
  "All time",
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asText = (value: unknown, fallback = ""): string =>
  typeof value === "string" && value.trim() ? value : fallback;

const normalizeNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string" && value.trim()) {
    const normalized = Number(value.replace(/[$,%\s,]/g, ""));
    return Number.isFinite(normalized) ? normalized : null;
  }

  return null;
};

const formatNumber = (value: unknown): string => {
  const numeric = normalizeNumber(value);
  if (numeric === null) {
    return "--";
  }

  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: Math.abs(numeric) >= 100 ? 0 : 1,
  }).format(numeric);
};

const humanizeIdentifier = (value: string): string => {
  const withoutIdSuffix = value.replace(/[_-]?id$/i, "");
  const spaced = withoutIdSuffix
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!spaced) {
    return "ID";
  }

  return spaced.replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const firstSentence = (value: string | null | undefined, maxLength = 52): string | null => {
  const text = value?.trim();
  if (!text) {
    return null;
  }

  const sentence = text.split(/[.!?]/)[0]?.trim() || text;
  return sentence.length > maxLength ? `${sentence.slice(0, maxLength - 3)}...` : sentence;
};

const getTableDisplayName = (
  schema: DatabaseSchema | undefined,
  tableName: string,
): string => {
  const table = schema?.tables.find((item) => item.table_name === tableName);
  return firstSentence(table?.table_description, 42) ?? humanizeIdentifier(tableName);
};

const getColumnDisplayName = (
  schema: DatabaseSchema | undefined,
  tableName: string,
  columnName: string,
): string => {
  const table = schema?.tables.find((item) => item.table_name === tableName);
  const column = table?.columns.find((item) => item.column_name === columnName);
  return (
    column?.nickname?.trim() ||
    firstSentence(column?.column_description, 42) ||
    humanizeIdentifier(columnName)
  );
};

const getRows = (card: DashboardCard): Record<string, unknown>[] =>
  Array.isArray(card.rows) ? card.rows.filter(isRecord) : [];

const getCards = (artifact: CopilotArtifact | null): DashboardCard[] => {
  const cards = artifact?.payload?.cards;
  return Array.isArray(cards) ? cards.filter(isRecord) : [];
};

const getArtifactWorkspace = (artifact: CopilotArtifact): string =>
  asText(artifact.payload?.workspace, "Default");

const getArtifactProject = (artifact: CopilotArtifact): string =>
  asText(artifact.payload?.project, "General");

const getCardTitle = (card: DashboardCard, index: number): string =>
  asText(card.title, `Metric ${index + 1}`);

const getCardDescription = (card: DashboardCard): string =>
  asText(card.description, "Vayent selected this metric from the connected source.");

const getCardExplanation = (card: DashboardCard): string =>
  asText(
    card.explanation,
    "This widget summarizes the current business signal in the selected source.",
  );

const getCardInterpretation = (card: DashboardCard): string =>
  asText(
    card.interpretation,
    getCardDescription(card),
  );

const getCardRecommendedAction = (card: DashboardCard): string =>
  asText(
    card.recommended_action,
    "Review the strongest and weakest segments, then decide where to focus next.",
  );

const getCardValue = (card: DashboardCard): number | null => {
  const directValue = normalizeNumber(card.value);
  if (directValue !== null) {
    return directValue;
  }

  const firstRow = getRows(card)[0];
  if (!firstRow) {
    return null;
  }

  for (const value of Object.values(firstRow)) {
    const numeric = normalizeNumber(value);
    if (numeric !== null) {
      return numeric;
    }
  }

  return null;
};

const getLabelValue = (value: unknown): string => {
  if (value instanceof Date) {
    return value.toLocaleDateString();
  }

  if (typeof value === "string" || typeof value === "number") {
    const text = String(value);
    return text.length > 18 ? `${text.slice(0, 18)}...` : text;
  }

  return "Result";
};

const chartKinds = new Set<ChartKind>([
  "kpi",
  "bar",
  "line",
  "pie",
  "donut",
  "area",
  "distribution",
  "heatmap",
  "funnel",
  "cohort",
  "forecast",
  "table",
]);

const normalizeChartKind = (value: unknown): ChartKind | null => {
  const normalized = asText(value).toLowerCase().replace(/\s+/g, "_");
  if (normalized === "column") {
    return "bar";
  }
  if (normalized === "trend") {
    return "line";
  }
  return chartKinds.has(normalized as ChartKind)
    ? (normalized as ChartKind)
    : null;
};

const selectSeries = (card: DashboardCard): SeriesPoint[] => {
  const rows = getRows(card).slice(0, 10);
  if (rows.length === 0) {
    const value = getCardValue(card);
    return value === null ? [] : [{ label: "Current", value }];
  }

  const keys = Array.from(
    rows.reduce((set, row) => {
      Object.keys(row).forEach((key) => set.add(key));
      return set;
    }, new Set<string>()),
  );

  const numericKey =
    keys
      .map((key) => ({
        key,
        count: rows.filter((row) => normalizeNumber(row[key]) !== null).length,
      }))
      .sort((left, right) => right.count - left.count)[0]?.key ?? keys[0];

  const labelKey =
    keys.find(
      (key) =>
        key !== numericKey &&
        rows.some((row) => normalizeNumber(row[key]) === null),
    ) ??
    keys.find((key) => key !== numericKey) ??
    numericKey;

  return rows
    .map((row, index) => ({
      label: getLabelValue(row[labelKey] ?? `Row ${index + 1}`),
      value: normalizeNumber(row[numericKey]) ?? 0,
    }))
    .filter((point) => Number.isFinite(point.value));
};

const inferChartKind = (card: DashboardCard): ChartKind => {
  const suggestedKind =
    normalizeChartKind(card.visualization) ?? normalizeChartKind(card.chart_type);
  if (suggestedKind) {
    return suggestedKind;
  }

  const rows = getRows(card);
  const title = `${asText(card.title)} ${asText(card.description)}`.toLowerCase();

  if (title.includes("distribution")) {
    return "distribution";
  }
  if (title.includes("share") || title.includes("mix") || title.includes("split")) {
    return "pie";
  }
  if (
    title.includes("trend") ||
    title.includes("growth") ||
    title.includes("over time") ||
    rows.some((row) =>
      Object.keys(row).some((key) => /date|month|week|year|day|time/i.test(key)),
    )
  ) {
    return title.includes("cumulative") ? "area" : "line";
  }
  if (rows.length > 1) {
    return "bar";
  }
  return "kpi";
};

const getWidgetId = (artifactId: string, card: DashboardCard, index: number) =>
  `${artifactId}:${index}:${getCardTitle(card, index)}`;

const detectDomain = (schema: DatabaseSchema | undefined, source: ConnectedSource | null) => {
  const metadataText = source?.metadata
    ? JSON.stringify(source.metadata).slice(0, 1200)
    : "";
  const haystack = [
    source?.name,
    source?.detail,
    source?.source_kind,
    metadataText,
    ...(schema?.tables ?? []).flatMap((table) => [
      table.table_name,
      ...table.columns.map((column) => column.column_name),
    ]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const domains = [
    {
      label: "E-commerce or marketplace",
      terms: ["order", "payment", "product", "cart", "customer", "refund"],
      kpis: ["Gross revenue", "Average order value", "Refund rate", "Repeat buyers"],
    },
    {
      label: "SaaS subscription",
      terms: ["subscription", "plan", "invoice", "trial", "churn", "usage"],
      kpis: ["MRR", "Activation rate", "Churn risk", "Expansion revenue"],
    },
    {
      label: "Customer operations",
      terms: ["ticket", "case", "agent", "sla", "queue", "resolution"],
      kpis: ["SLA health", "Backlog", "First response time", "Resolution trend"],
    },
    {
      label: "Finance or ledger",
      terms: ["transaction", "account", "balance", "ledger", "expense", "payout"],
      kpis: ["Net inflow", "Expense mix", "Outstanding balance", "Anomaly rate"],
    },
    {
      label: "Product analytics",
      terms: ["event", "session", "feature", "activity", "login", "cohort"],
      kpis: ["Active users", "Feature adoption", "Retention", "Engagement depth"],
    },
  ];

  const scored = domains
    .map((domain) => ({
      ...domain,
      score: domain.terms.filter((term) => haystack.includes(term)).length,
    }))
    .sort((left, right) => right.score - left.score);

  const best = scored[0];
  if (!best || best.score === 0) {
    return {
      label: "General analytics workspace",
      confidence: 58,
      kpis: ["Volume", "Growth", "Quality", "Operational health"],
    };
  }

  return {
    label: best.label,
    confidence: Math.min(96, 62 + best.score * 8),
    kpis: best.kpis,
  };
};

const getSchemaStats = (schema: DatabaseSchema | undefined) => {
  const tables = schema?.tables ?? [];
  const columns = tables.flatMap((table) => table.columns);
  return {
    tables: tables.length,
    columns: columns.length,
    relationships: schema?.relationships.length ?? 0,
    nullableColumns: columns.filter((column) => column.is_nullable).length,
    primaryKeys: columns.filter((column) => column.is_primary_key).length,
    foreignKeys: columns.filter((column) => column.is_foreign_key).length,
    totalRows: tables.reduce((sum, table) => sum + (table.row_count ?? 0), 0),
  };
};

const getSourceMetadataTables = (
  source: ConnectedSource | null,
): Record<string, unknown>[] => {
  const tables = source?.metadata?.tables;
  return Array.isArray(tables) ? tables.filter(isRecord) : [];
};

const getProfileTableName = (table: Record<string, unknown>, fallback: string) =>
  asText(table.name, fallback);

const getProfileTableColumns = (table: Record<string, unknown>) => {
  const columns = table.columns;
  return Array.isArray(columns) ? columns.filter(isRecord) : [];
};

const getSourceStats = (
  schema: DatabaseSchema | undefined,
  source: ConnectedSource | null,
) => {
  if (source?.source_type !== "spreadsheet") {
    return getSchemaStats(schema);
  }

  const tables = getSourceMetadataTables(source);
  const columns = tables.flatMap(getProfileTableColumns);
  return {
    tables: tables.length,
    columns: columns.length,
    relationships: 0,
    nullableColumns: columns.filter((column) => Boolean(column.nullable)).length,
    primaryKeys: 0,
    foreignKeys: 0,
    totalRows: tables.reduce(
      (sum, table) => sum + (normalizeNumber(table.row_count) ?? 0),
      0,
    ),
  };
};

const buildInsights = (
  cards: DashboardCard[],
  schema: DatabaseSchema | undefined,
  source: ConnectedSource | null,
): InsightItem[] => {
  const insights: InsightItem[] = [];
  const successfulCards = cards.filter((card) => asText(card.status, "success") !== "error");

  cards.forEach((card, index) => {
    const series = selectSeries(card);
    if (series.length < 2 || insights.length >= 3) {
      return;
    }

    const previous = series[series.length - 2].value;
    const latest = series[series.length - 1].value;
    if (previous === 0) {
      return;
    }

    const delta = ((latest - previous) / Math.abs(previous)) * 100;
    if (Math.abs(delta) >= 12) {
      insights.push({
        title: getCardTitle(card, index),
        body: `${delta < 0 ? "Dropped" : "Improved"} by ${Math.abs(delta).toFixed(
          1,
        )}% between the latest two visible periods.`,
        tone: delta < 0 ? "warning" : "positive",
      });
    }
  });

  if (successfulCards.length > 0) {
    const standout = successfulCards
      .map((card, index) => ({
        title: getCardTitle(card, index),
        value: getCardValue(card),
        interpretation: getCardInterpretation(card),
      }))
      .find((item) => item.value !== null);
    insights.push({
      title: standout?.title ?? "Business signals are ready",
      body: standout
        ? `${standout.title} is currently ${formatNumber(standout.value)}. ${standout.interpretation}`
        : `Vayent found ${successfulCards.length} usable business signal${successfulCards.length === 1 ? "" : "s"} in ${source?.name ?? "the selected source"}.`,
      tone: "positive",
    });
  }

  const stats = getSourceStats(schema, source);
  if (stats.nullableColumns > Math.max(6, stats.columns * 0.35)) {
    insights.push({
      title: "Some evidence may be incomplete",
      body: "Several business fields appear to be optional or sparsely filled. Treat changes in customer, revenue, or event metrics as directional until the source is refreshed.",
      tone: "warning",
    });
  }

  if (insights.length === 0) {
    insights.push({
      title: "Ready for analysis",
      body: "Create a dashboard to let Vayent inspect the source evidence, summarize what changed, and recommend what to do next.",
      tone: "neutral",
    });
  }

  return insights.slice(0, 5);
};

const buildRecommendations = (
  cards: DashboardCard[],
  schema: DatabaseSchema | undefined,
  source: ConnectedSource | null,
): RecommendationItem[] => {
  const stats = getSourceStats(schema, source);
  const domain = detectDomain(schema, source);
  const failedCards = cards.filter((card) => asText(card.status) === "error");
  const recommendations: RecommendationItem[] = [
    {
      title: "Track the next KPI set",
      body: `${domain.kpis.slice(0, 3).join(", ")} are the strongest suggested operating metrics for this source.`,
      priority: "High",
    },
  ];

  if (failedCards.length > 0) {
    recommendations.push({
      title: "Refresh the source",
      body: `${failedCards.length} widget${failedCards.length === 1 ? "" : "s"} needs fresh evidence. Refresh the source, then regenerate the dashboard.`,
      priority: "High",
    });
  }

  if (cards.some((card) => getCardRecommendedAction(card))) {
    recommendations.push({
      title: "Act on the strongest signal",
      body: getCardRecommendedAction(cards[0] ?? {}),
      priority: "Medium",
    });
  }

  recommendations.push({
    title: "Create a recurring executive view",
    body: "Use the conversational builder to keep a saved dashboard for growth, retention, revenue, and risk instead of replacing prior dashboards.",
    priority: "Medium",
  });

  if (stats.nullableColumns > 0) {
    recommendations.push({
      title: "Tighten source quality",
      body: "Add checks for missing customer, revenue, date, and status fields before relying on forecasts or automated follow-up.",
      priority: "Low",
    });
  }

  return recommendations.slice(0, 5);
};

const normalizeInsightTone = (value: unknown): InsightItem["tone"] => {
  const tone = asText(value).toLowerCase();
  if (tone.includes("warn") || tone.includes("risk") || tone.includes("negative")) {
    return "warning";
  }
  if (tone.includes("positive") || tone.includes("success") || tone.includes("up")) {
    return "positive";
  }
  return "neutral";
};

const normalizePriority = (value: unknown): RecommendationItem["priority"] => {
  const priority = asText(value).toLowerCase();
  if (priority === "high") {
    return "High";
  }
  if (priority === "low") {
    return "Low";
  }
  return "Medium";
};

const getPayloadInsights = (artifact: CopilotArtifact | null): InsightItem[] => {
  const items = artifact?.payload?.insights;
  if (!Array.isArray(items)) {
    return [];
  }

  return items
    .filter(isRecord)
    .map((item) => ({
      title: asText(item.title, "Business signal"),
      body: asText(item.body ?? item.summary ?? item.description, ""),
      tone: normalizeInsightTone(item.tone ?? item.priority),
    }))
    .filter((item) => item.body)
    .slice(0, 5);
};

const getPayloadRecommendations = (
  artifact: CopilotArtifact | null,
): RecommendationItem[] => {
  const items = artifact?.payload?.recommendations;
  if (!Array.isArray(items)) {
    return [];
  }

  return items
    .filter(isRecord)
    .map((item) => ({
      title: asText(item.title, "Recommended action"),
      body: asText(item.body ?? item.summary ?? item.description, ""),
      priority: normalizePriority(item.priority),
    }))
    .filter((item) => item.body)
    .slice(0, 5);
};

const getRangeStart = (range: string): Date | null => {
  const now = new Date();
  if (range === "Last 7 days") {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
  }
  if (range === "Last 30 days") {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 30);
  }
  if (range === "Quarter to date") {
    const quarterStartMonth = Math.floor(now.getMonth() / 3) * 3;
    return new Date(now.getFullYear(), quarterStartMonth, 1);
  }
  if (range === "Year to date") {
    return new Date(now.getFullYear(), 0, 1);
  }
  return null;
};

const rowDateValue = (row: Record<string, unknown>): Date | null => {
  for (const [key, value] of Object.entries(row)) {
    if (!/date|month|week|year|day|time|created|updated/i.test(key)) {
      continue;
    }
    if (typeof value !== "string" && typeof value !== "number") {
      continue;
    }
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed;
    }
  }
  return null;
};

const summarizeFilteredValue = (rows: Record<string, unknown>[]): number | null => {
  if (rows.length === 0) {
    return null;
  }

  const numericKey = Object.keys(rows[0]).find((key) =>
    rows.some((row) => normalizeNumber(row[key]) !== null),
  );
  if (!numericKey) {
    return null;
  }

  return rows.reduce((sum, row) => sum + (normalizeNumber(row[numericKey]) ?? 0), 0);
};

const filterCardsByDateRange = (
  cards: DashboardCard[],
  range: string,
): DashboardCard[] => {
  const start = getRangeStart(range);
  if (!start) {
    return cards;
  }

  return cards.map((card) => {
    const rows = getRows(card);
    if (rows.length === 0) {
      return card;
    }

    const datedRows = rows.filter((row) => rowDateValue(row));
    if (datedRows.length === 0) {
      return card;
    }

    const filteredRows = rows.filter((row) => {
      const rowDate = rowDateValue(row);
      return rowDate ? rowDate >= start : true;
    });

    return {
      ...card,
      rows: filteredRows,
      row_count: filteredRows.length,
      value: summarizeFilteredValue(filteredRows) ?? card.value,
    };
  });
};

const escapeCsvCell = (value: unknown): string => {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

const buildCsv = (artifact: CopilotArtifact, cards: DashboardCard[]) => {
  const rows: Record<string, unknown>[] = cards.flatMap((card, index) =>
    getRows(card).map((row) => ({
      dashboard: artifact.title,
      widget: getCardTitle(card, index),
      ...row,
    })),
  );

  if (rows.length === 0) {
    return "dashboard,widget,value\n" +
      cards
        .map((card, index) =>
          [
            artifact.title,
            getCardTitle(card, index),
            formatNumber(getCardValue(card)),
          ]
            .map(escapeCsvCell)
            .join(","),
        )
        .join("\n");
  }

  const headers = Array.from(
    rows.reduce((set, row) => {
      Object.keys(row).forEach((key) => set.add(key));
      return set;
    }, new Set<string>()),
  );

  return [
    headers.map((header) => escapeCsvCell(humanizeIdentifier(header))).join(","),
    ...rows.map((row) => headers.map((key) => escapeCsvCell(row[key])).join(",")),
  ].join("\n");
};

const downloadCsv = (artifact: CopilotArtifact, cards: DashboardCard[]) => {
  const blob = new Blob([buildCsv(artifact, cards)], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${artifact.title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

const renderAxisLabel = (text: string): string =>
  text.length > 10 ? `${text.slice(0, 10)}...` : text;

const MiniBarChart: React.FC<{ series: SeriesPoint[] }> = ({ series }) => {
  const max = Math.max(...series.map((point) => Math.abs(point.value)), 1);

  return (
    <svg className="analytics-chart-svg" viewBox="0 0 420 180" role="img">
      {series.map((point, index) => {
        const width = Math.max(8, (Math.abs(point.value) / max) * 220);
        const y = 20 + index * (140 / Math.max(series.length, 1));
        return (
          <g key={`${point.label}-${index}`}>
            <title>{`${point.label}: ${formatNumber(point.value)}`}</title>
            <text x="12" y={y + 12} className="analytics-chart-label">
              {renderAxisLabel(point.label)}
            </text>
            <rect
              x="130"
              y={y}
              width={width}
              height="16"
              rx="4"
              fill={chartPalette[index % chartPalette.length]}
            />
            <text x={Math.min(390, 140 + width)} y={y + 12} className="analytics-chart-value">
              {formatNumber(point.value)}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

const MiniLineChart: React.FC<{ series: SeriesPoint[]; area?: boolean }> = ({
  series,
  area,
}) => {
  const max = Math.max(...series.map((point) => point.value), 1);
  const min = Math.min(...series.map((point) => point.value), 0);
  const range = Math.max(max - min, 1);
  const points = series.map((point, index) => {
    const x = 24 + (index / Math.max(series.length - 1, 1)) * 360;
    const y = 142 - ((point.value - min) / range) * 108;
    return { x, y, ...point };
  });
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
  const areaPath = `${path} L ${points[points.length - 1]?.x ?? 384} 154 L 24 154 Z`;

  return (
    <svg className="analytics-chart-svg" viewBox="0 0 420 180" role="img">
      <line x1="24" y1="154" x2="392" y2="154" className="analytics-grid-line" />
      {area ? <path d={areaPath} className="analytics-area-fill" /> : null}
      <path d={path} className="analytics-line-path" />
      {points.map((point, index) => (
        <g key={`${point.label}-${index}`}>
          <title>{`${point.label}: ${formatNumber(point.value)}`}</title>
          <circle cx={point.x} cy={point.y} r="4" fill="#4f46e5" />
          <text x={point.x - 18} y="172" className="analytics-chart-label">
            {index % 2 === 0 ? renderAxisLabel(point.label) : ""}
          </text>
        </g>
      ))}
    </svg>
  );
};

const MiniPieChart: React.FC<{ series: SeriesPoint[] }> = ({ series }) => {
  const total = series.reduce((sum, point) => sum + Math.max(point.value, 0), 0) || 1;
  const gradient = series.reduce(
    (state, point, index) => {
      const end = state.cursor + (Math.max(point.value, 0) / total) * 100;
      return {
        cursor: end,
        parts: [
          ...state.parts,
          `${chartPalette[index % chartPalette.length]} ${state.cursor}% ${end}%`,
        ],
      };
    },
    { cursor: 0, parts: [] as string[] },
  ).parts.join(", ");

  return (
    <div className="analytics-pie-wrap">
      <div
        className="analytics-pie"
        style={{ background: `conic-gradient(${gradient})` }}
      />
      <div className="analytics-pie-legend">
        {series.slice(0, 5).map((point, index) => (
          <span key={`${point.label}-${index}`} title={`${point.label}: ${formatNumber(point.value)}`}>
            <i style={{ backgroundColor: chartPalette[index % chartPalette.length] }} />
            {point.label}
          </span>
        ))}
      </div>
    </div>
  );
};

const AnalyticsChart: React.FC<{ card: DashboardCard; kind: ChartKind }> = ({
  card,
  kind,
}) => {
  const series = selectSeries(card);
  const value = getCardValue(card);

  if (asText(card.status) === "error") {
    return (
      <div className="analytics-widget-empty analytics-widget-error">
        {asText(card.error, "The evidence query failed.")}
      </div>
    );
  }

  if (kind === "kpi") {
    return (
      <div className="analytics-kpi-body">
        <span>{formatNumber(value)}</span>
        <small>{getRows(card).length} evidence rows</small>
      </div>
    );
  }

  if (series.length === 0) {
    return <div className="analytics-widget-empty">No chartable rows returned.</div>;
  }

  if (kind === "line" || kind === "forecast" || kind === "cohort") {
    return <MiniLineChart series={series} />;
  }
  if (kind === "area") {
    return <MiniLineChart series={series} area />;
  }
  if (kind === "pie" || kind === "donut") {
    return <MiniPieChart series={series} />;
  }
  if (kind === "table") {
    return (
      <pre className="code-block app-scroll-x">
        {JSON.stringify(getRows(card).slice(0, 8), null, 2)}
      </pre>
    );
  }
  return <MiniBarChart series={series} />;
};

const DashboardPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const currentUser = useAuthStore((state) => state.user);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [selectedDashboardId, setSelectedDashboardId] = useState("");
  const [dashboardPrompt, setDashboardPrompt] = useState(
    "Create an executive analytics dashboard for growth, revenue, activity, data quality, and operational risk.",
  );
  const [mode, setMode] = useState<DashboardMode>("create");
  const [workspaceFilter, setWorkspaceFilter] = useState("All");
  const [searchTerm, setSearchTerm] = useState("");
  const [dateRange, setDateRange] = useState(dateRanges[1]);
  const [chartFilter, setChartFilter] = useState<ChartKind | "all">("all");
  const [realtimeEnabled, setRealtimeEnabled] = useState(false);
  const [draggedWidgetId, setDraggedWidgetId] = useState<string | null>(null);
  const [widgetOrder, setWidgetOrder] = useState<string[]>([]);
  const [titleDraft, setTitleDraft] = useState("");
  const [workspaceDraft, setWorkspaceDraft] = useState("Default");
  const [projectDraft, setProjectDraft] = useState("General");
  const [feedback, setFeedback] = useState<string | null>(null);
  const dashboardTokenAvailable =
    currentUser?.remaining_tokens === null ||
    currentUser?.remaining_tokens === undefined ||
    currentUser.remaining_tokens > 0;
  const tokenStatusLabel =
    currentUser?.remaining_tokens === null ||
    currentUser?.remaining_tokens === undefined
      ? `${currentUser?.daily_token_usage?.toLocaleString() ?? "0"} tokens used today`
      : `${currentUser.remaining_tokens.toLocaleString()} tokens left`;

  const { data: sourceList, isLoading: connectionsLoading } = useQuery<
    ConnectedSourceList
  >({
    queryKey: ["connected-sources"],
    queryFn: async () => {
      const res = await api.get("/connections/sources");
      return res.data as ConnectedSourceList;
    },
  });

  const connections = useMemo(() => sourceList?.items ?? [], [sourceList?.items]);
  const requestedSourceId = searchParams.get("sourceId");

  useEffect(() => {
    if (
      selectedConnectionId &&
      connections.some((source) => source.id === selectedConnectionId)
    ) {
      return;
    }

    const storedConnectionId = getActiveConnectionId();
    const nextConnectionId =
      [requestedSourceId, storedConnectionId, connections[0]?.id].find(
        (sourceId): sourceId is string =>
          Boolean(
            sourceId &&
              connections.some((source) => source.id === sourceId),
          ),
      ) ?? "";

    if (nextConnectionId) {
      setSelectedConnectionId(nextConnectionId);
    }
  }, [connections, requestedSourceId, selectedConnectionId]);

  useEffect(() => {
    const selectedSource = connections.find(
      (source) => source.id === selectedConnectionId,
    );
    if (selectedConnectionId && selectedSource?.source_type === "database") {
      setActiveConnectionId(selectedConnectionId);
    } else if (selectedConnectionId) {
      setActiveConnectionId(null);
    }
  }, [connections, selectedConnectionId]);

  const selectedConnection = useMemo(
    () =>
      connections.find((source) => source.id === selectedConnectionId) ??
      null,
    [connections, selectedConnectionId],
  );
  const selectedSourceIsDatabase = selectedConnection?.source_type === "database";

  const { data: schema, isFetching: schemaFetching } = useQuery<DatabaseSchema>({
    queryKey: ["schema", selectedConnectionId],
    enabled: Boolean(selectedConnectionId && selectedSourceIsDatabase),
    retry: false,
    queryFn: async () => {
      const res = await api.get(`/connections/${selectedConnectionId}/schema`);
      return res.data as DatabaseSchema;
    },
  });

  const { data: artifactResponse } = useQuery<CopilotArtifactList>({
    queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
    enabled: Boolean(selectedConnectionId),
    queryFn: async () => {
      const res = await api.get("/copilot/artifacts", {
        params: {
          source_id: selectedConnectionId,
          artifact_type: "dashboard",
        },
      });
      return res.data as CopilotArtifactList;
    },
  });

  const dashboards = useMemo(
    () =>
      (artifactResponse?.items ?? []).filter(
        (artifact) => artifact.artifact_type === "dashboard",
      ),
    [artifactResponse?.items],
  );

  const workspaces = useMemo(
    () => ["All", ...Array.from(new Set(dashboards.map(getArtifactWorkspace)))],
    [dashboards],
  );

  const visibleDashboards = useMemo(() => {
    return dashboards.filter((dashboard) => {
      const matchesWorkspace =
        workspaceFilter === "All" ||
        getArtifactWorkspace(dashboard) === workspaceFilter;
      const matchesSearch =
        !searchTerm.trim() ||
        `${dashboard.title} ${dashboard.summary ?? ""} ${getArtifactProject(
          dashboard,
        )}`
          .toLowerCase()
          .includes(searchTerm.trim().toLowerCase());

      return matchesWorkspace && matchesSearch;
    });
  }, [dashboards, searchTerm, workspaceFilter]);

  useEffect(() => {
    if (
      selectedDashboardId &&
      dashboards.some((dashboard) => dashboard.id === selectedDashboardId)
    ) {
      return;
    }

    setSelectedDashboardId(dashboards[0]?.id ?? "");
  }, [dashboards, selectedDashboardId]);

  const activeDashboard = useMemo(
    () =>
      dashboards.find((dashboard) => dashboard.id === selectedDashboardId) ??
      visibleDashboards[0] ??
      null,
    [dashboards, selectedDashboardId, visibleDashboards],
  );

  const rawCards = useMemo(() => getCards(activeDashboard), [activeDashboard]);
  const filteredCards = useMemo(
    () => filterCardsByDateRange(rawCards, dateRange),
    [dateRange, rawCards],
  );

  useEffect(() => {
    if (!activeDashboard) {
      setTitleDraft("");
      setWorkspaceDraft("Default");
      setProjectDraft("General");
      return;
    }

    setTitleDraft(activeDashboard.title);
    setWorkspaceDraft(getArtifactWorkspace(activeDashboard));
    setProjectDraft(getArtifactProject(activeDashboard));
  }, [activeDashboard]);

  useEffect(() => {
    if (!activeDashboard) {
      setWidgetOrder([]);
      return;
    }

    const ids = filteredCards.map((card, index) =>
      getWidgetId(activeDashboard.id, card, index),
    );
    setWidgetOrder((previous) => [
      ...previous.filter((id) => ids.includes(id)),
      ...ids.filter((id) => !previous.includes(id)),
    ]);
  }, [activeDashboard, filteredCards]);

  useEffect(() => {
    if (!realtimeEnabled || !selectedConnectionId) {
      return undefined;
    }

    const interval = window.setInterval(() => {
      void queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
    }, 30000);

    return () => window.clearInterval(interval);
  }, [queryClient, realtimeEnabled, selectedConnectionId]);

  const createDashboardMutation = useMutation<CopilotArtifact, Error, string>({
    mutationFn: async (prompt) => {
      const finalPrompt =
        mode === "modify" && activeDashboard
          ? `Revise the saved dashboard "${activeDashboard.title}" for this request: ${prompt}. Preserve useful widgets, add stronger evidence, and return the revised dashboard as a new saved version.`
          : prompt;
      const res = await api.post("/copilot/dashboards", {
        connection_id: selectedSourceIsDatabase ? selectedConnectionId : undefined,
        source_id: selectedConnectionId,
        source_ids: [selectedConnectionId],
        prompt: finalPrompt,
      });
      return res.data as CopilotArtifact;
    },
    onSuccess: async (artifact) => {
      await queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      setSelectedDashboardId(artifact.id);
      setFeedback("Dashboard generated and saved.");
    },
    onError: (error) => setFeedback(error.message),
  });

  const syncSchemaMutation = useMutation<SyncSourceResponse, Error>({
    mutationFn: async () => {
      const res = await api.post(`/connections/sources/${selectedConnectionId}/sync`);
      return res.data as SyncSourceResponse;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["schema", selectedConnectionId],
      });
      await queryClient.invalidateQueries({ queryKey: ["connected-sources"] });
      await queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
      setFeedback("Source synced. Vayent can regenerate dashboards with fresh evidence.");
    },
    onError: (error) => setFeedback(error.message),
  });

  const updateArtifactMutation = useMutation<
    CopilotArtifact,
    Error,
    { id: string; title?: string; workspace?: string; project?: string }
  >({
    mutationFn: async ({ id, ...payload }) => {
      const res = await api.patch(`/copilot/artifacts/${id}`, payload);
      return res.data as CopilotArtifact;
    },
    onSuccess: async (artifact) => {
      await queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
      setSelectedDashboardId(artifact.id);
      setFeedback("Dashboard details saved.");
    },
    onError: (error) => setFeedback(error.message),
  });

  const duplicateArtifactMutation = useMutation<CopilotArtifact, Error, string>({
    mutationFn: async (id) => {
      const res = await api.post(`/copilot/artifacts/${id}/duplicate`);
      return res.data as CopilotArtifact;
    },
    onSuccess: async (artifact) => {
      await queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
      setSelectedDashboardId(artifact.id);
      setFeedback("Dashboard duplicated.");
    },
    onError: (error) => setFeedback(error.message),
  });

  const deleteArtifactMutation = useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await api.delete(`/copilot/artifacts/${id}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["copilot-artifacts", selectedConnectionId, "dashboard"],
      });
      setFeedback("Dashboard deleted.");
    },
    onError: (error) => setFeedback(error.message),
  });

  const sourceStats = getSourceStats(schema, selectedConnection);
  const sourceTables = getSourceMetadataTables(selectedConnection);
  const sourceStatusLabel = selectedConnection
    ? selectedConnection.source_type === "spreadsheet"
      ? selectedConnection.status === "error"
        ? "Needs attention"
        : "Profile ready"
      : schemaFetching
        ? "Reading"
        : schema
          ? "Schema ready"
          : "Needs sync"
    : "No source";
  const sourceSyncLabel =
    selectedConnection?.source_type === "spreadsheet"
      ? selectedConnection.source_kind === "link"
        ? "Sync link"
        : "Refresh file"
      : "Sync source";
  const domain = detectDomain(schema, selectedConnection);
  const payloadInsights = getPayloadInsights(activeDashboard);
  const payloadRecommendations = getPayloadRecommendations(activeDashboard);
  const insights =
    payloadInsights.length > 0
      ? payloadInsights
      : buildInsights(filteredCards, schema, selectedConnection);
  const recommendations =
    payloadRecommendations.length > 0
      ? payloadRecommendations
      : buildRecommendations(filteredCards, schema, selectedConnection);

  const orderedCards = useMemo(() => {
    if (!activeDashboard) {
      return [];
    }

    const cardEntries = filteredCards.map((card, index) => ({
      card,
      index,
      id: getWidgetId(activeDashboard.id, card, index),
      kind: inferChartKind(card),
    }));
    const ordered = widgetOrder
      .map((id) => cardEntries.find((entry) => entry.id === id))
      .filter((entry): entry is (typeof cardEntries)[number] => Boolean(entry));

    return ordered.filter(
      (entry) => chartFilter === "all" || entry.kind === chartFilter,
    );
  }, [activeDashboard, chartFilter, filteredCards, widgetOrder]);

  const handleDropWidget = (targetId: string) => {
    if (!draggedWidgetId || draggedWidgetId === targetId) {
      return;
    }

    setWidgetOrder((previous) => {
      const next = previous.filter((id) => id !== draggedWidgetId);
      const targetIndex = next.indexOf(targetId);
      next.splice(Math.max(targetIndex, 0), 0, draggedWidgetId);
      return next;
    });
    setDraggedWidgetId(null);
  };

  const handleSaveOrganization = () => {
    if (!activeDashboard) {
      return;
    }

    updateArtifactMutation.mutate({
      id: activeDashboard.id,
      title: titleDraft,
      workspace: workspaceDraft,
      project: projectDraft,
    });
  };

  const handleCreateDashboard = (prompt = dashboardPrompt) => {
    if (!selectedConnectionId || !prompt.trim()) {
      return;
    }
    if (!dashboardTokenAvailable) {
      setFeedback("Dashboard generation uses your account tokens. Your available balance is currently exhausted.");
      return;
    }
    createDashboardMutation.mutate(prompt.trim());
  };

  if (connectionsLoading) {
    return (
      <div className="app-page analytics-page">
        <div className="app-empty">Loading analytics workspace...</div>
      </div>
    );
  }

  if (connections.length === 0) {
    return (
      <div className="app-page analytics-page">
        <section className="analytics-empty-source">
          <div>
            <p className="page-kicker">AI Analytics</p>
            <h1>Connect a source to generate analytics dashboards.</h1>
            <p>
              Vayent needs one live source before it can profile business
              patterns, generate widgets, and recommend next steps.
            </p>
          </div>
          <Link to="/connections" className="brand-btn-primary">
            Add source
          </Link>
        </section>
      </div>
    );
  }

  return (
    <div className="app-page analytics-page">
      <section className="analytics-command">
        <div className="analytics-command-copy">
          <p className="page-kicker">AI Analytics Dashboard</p>
          <h1>Autonomous BI for connected business sources.</h1>
          <p>
            Select a source, refresh the latest evidence, and let Vayent create
            saved dashboards with business-friendly labels, charts, insights,
            and recommendations. Generation uses your available account tokens.
          </p>

          <div className="analytics-control-grid">
            <div className="analytics-field">
              <label htmlFor="analytics-connection">Source</label>
              <div className="glass-select-wrap">
                <select
                  id="analytics-connection"
                  className="input glass-select"
                  value={selectedConnectionId}
                  onChange={(event) => {
                    setSelectedConnectionId(event.target.value);
                    setSelectedDashboardId("");
                  }}
                >
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name} ({connection.source_type})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <button
              type="button"
              className="brand-btn-secondary"
              onClick={() => syncSchemaMutation.mutate()}
              disabled={!selectedConnectionId || syncSchemaMutation.isPending}
            >
              {syncSchemaMutation.isPending ? "Syncing..." : sourceSyncLabel}
            </button>

            <button
              type="button"
              className="brand-btn-primary"
              onClick={() => handleCreateDashboard()}
              disabled={
                !selectedConnectionId ||
                createDashboardMutation.isPending ||
                !dashboardPrompt.trim() ||
                !dashboardTokenAvailable
              }
            >
              {createDashboardMutation.isPending
                ? "Generating..."
                : "Create dashboard"}
            </button>
          </div>

          <textarea
            className="input analytics-prompt"
            value={dashboardPrompt}
            onChange={(event) => setDashboardPrompt(event.target.value)}
          />

          <div className="analytics-pipeline">
            {[
              ["Source", selectedConnection ? selectedConnection.source_type : "No source"],
              ["Coverage", `${sourceStats.tables} ${selectedSourceIsDatabase ? "tables" : "sheets"}`],
              ["Domain", `${domain.label} (${domain.confidence}%)`],
              ["Token balance", tokenStatusLabel],
              ["Artifacts", `${dashboards.length} saved`],
            ].map(([label, value]) => (
              <div key={label} className="analytics-pipeline-step">
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        </div>

        <aside className="analytics-source-panel">
          <div className="analytics-source-head">
            <div>
              <p className="page-kicker">Source Intelligence</p>
              <h2 title={selectedConnection?.name ?? "No source"}>
                {selectedConnection?.name ?? "No source"}
              </h2>
            </div>
            <span className="brand-badge">{sourceStatusLabel}</span>
          </div>

          <div className="analytics-source-grid">
            <div>
              <span>{selectedSourceIsDatabase ? "Tables" : "Sheets"}</span>
              <strong>{sourceStats.tables}</strong>
            </div>
            <div>
              <span>Fields</span>
              <strong>{sourceStats.columns}</strong>
            </div>
            <div>
              <span>{selectedSourceIsDatabase ? "Links" : "Insights"}</span>
              <strong>
                {selectedSourceIsDatabase
                  ? sourceStats.foreignKeys
                  : formatNumber(selectedConnection?.metadata.insight_count)}
              </strong>
            </div>
            <div>
              <span>Rows seen</span>
              <strong>{formatNumber(sourceStats.totalRows)}</strong>
            </div>
          </div>

          <div className="analytics-entity-list">
            {selectedSourceIsDatabase
              ? (schema?.tables ?? []).slice(0, 6).map((table) => (
                  <span key={table.id} title={getTableDisplayName(schema, table.table_name)}>
                    {getTableDisplayName(schema, table.table_name)}
                    <small>{table.columns.length} fields</small>
                  </span>
                ))
              : sourceTables.slice(0, 6).map((table, index) => (
                  <span
                    key={`${getProfileTableName(table, `Sheet ${index + 1}`)}-${index}`}
                    title={getProfileTableName(table, `Sheet ${index + 1}`)}
                  >
                    {getProfileTableName(table, `Sheet ${index + 1}`)}
                    <small>{getProfileTableColumns(table).length} fields</small>
                  </span>
                ))}
            {selectedSourceIsDatabase && !schema ? (
              <span>Source metadata has not been loaded.</span>
            ) : null}
            {!selectedSourceIsDatabase && sourceTables.length === 0 ? (
              <span>Spreadsheet profile has not been loaded.</span>
            ) : null}
          </div>
        </aside>
      </section>

      {feedback ? <div className="analytics-feedback">{feedback}</div> : null}

      <section className="analytics-workspace">
        <aside className="analytics-library">
          <div className="analytics-section-head">
            <div>
              <p className="page-kicker">Dashboard Library</p>
              <h2>Saved dashboards</h2>
            </div>
            <span className="brand-pill">{visibleDashboards.length}</span>
          </div>

          <div className="analytics-filter-stack">
            <div className="glass-select-wrap">
              <select
                className="input glass-select"
                value={workspaceFilter}
                onChange={(event) => setWorkspaceFilter(event.target.value)}
              >
                {workspaces.map((workspace) => (
                  <option key={workspace} value={workspace}>
                    {workspace}
                  </option>
                ))}
              </select>
            </div>
            <input
              className="input"
              placeholder="Search dashboards"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
            />
          </div>

          <div className="analytics-dashboard-list app-scroll-panel">
            {visibleDashboards.length === 0 ? (
              <div className="analytics-mini-empty">
                No dashboards saved for this view.
              </div>
            ) : (
              visibleDashboards.map((dashboard) => (
                <button
                  key={dashboard.id}
                  type="button"
                  title={dashboard.title}
                  className={`analytics-dashboard-item ${
                    activeDashboard?.id === dashboard.id
                      ? "analytics-dashboard-item-active"
                      : ""
                  }`}
                  onClick={() => setSelectedDashboardId(dashboard.id)}
                >
                  <span>{getArtifactWorkspace(dashboard)}</span>
                  <strong>{dashboard.title}</strong>
                  <small>
                    {getCards(dashboard).length} widgets -{" "}
                    {new Date(dashboard.created_at).toLocaleDateString()}
                  </small>
                </button>
              ))
            )}
          </div>
        </aside>

        <main className="analytics-dashboard-surface">
          {activeDashboard ? (
            <>
              <div className="analytics-dashboard-toolbar">
                <div className="analytics-title-edit">
                  <label htmlFor="dashboard-title">Dashboard</label>
                  <input
                    id="dashboard-title"
                    className="input"
                    value={titleDraft}
                    onChange={(event) => setTitleDraft(event.target.value)}
                  />
                </div>
                <div className="analytics-org-grid">
                  <input
                    className="input"
                    value={workspaceDraft}
                    onChange={(event) => setWorkspaceDraft(event.target.value)}
                    aria-label="Workspace"
                  />
                  <input
                    className="input"
                    value={projectDraft}
                    onChange={(event) => setProjectDraft(event.target.value)}
                    aria-label="Project"
                  />
                </div>
                <div className="analytics-toolbar-actions">
                  <button
                    type="button"
                    className="brand-btn-secondary"
                    onClick={handleSaveOrganization}
                    disabled={updateArtifactMutation.isPending || !titleDraft.trim()}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="brand-btn-secondary"
                    onClick={() => duplicateArtifactMutation.mutate(activeDashboard.id)}
                    disabled={duplicateArtifactMutation.isPending}
                  >
                    Duplicate
                  </button>
                  <button
                    type="button"
                    className="brand-btn-danger"
                    onClick={() => deleteArtifactMutation.mutate(activeDashboard.id)}
                    disabled={deleteArtifactMutation.isPending}
                  >
                    Delete
                  </button>
                </div>
              </div>

              <div className="analytics-dashboard-meta">
                <span>{getArtifactProject(activeDashboard)}</span>
                <span>{dateRange}</span>
                <span>{filteredCards.length} AI-selected widgets</span>
                <span>
                  Updated{" "}
                  {new Date(
                    activeDashboard.updated_at ?? activeDashboard.created_at,
                  ).toLocaleString()}
                </span>
              </div>

              <div className="analytics-dashboard-controls">
                <div className="glass-select-wrap">
                  <select
                    className="input glass-select"
                    value={dateRange}
                    onChange={(event) => setDateRange(event.target.value)}
                  >
                    {dateRanges.map((range) => (
                      <option key={range} value={range}>
                        {range}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="glass-select-wrap">
                  <select
                    className="input glass-select"
                    value={chartFilter}
                    onChange={(event) =>
                      setChartFilter(event.target.value as ChartKind | "all")
                    }
                  >
                    <option value="all">All chart types</option>
                    <option value="kpi">KPI cards</option>
                    <option value="bar">Bar charts</option>
                    <option value="line">Line charts</option>
                    <option value="pie">Pie charts</option>
                    <option value="donut">Donut charts</option>
                    <option value="area">Area charts</option>
                    <option value="distribution">Distribution charts</option>
                    <option value="heatmap">Heatmaps</option>
                    <option value="funnel">Funnels</option>
                    <option value="cohort">Cohorts</option>
                    <option value="forecast">Forecasts</option>
                    <option value="table">Tables</option>
                  </select>
                </div>

                <label className="analytics-toggle">
                  <input
                    type="checkbox"
                    checked={realtimeEnabled}
                    onChange={(event) => setRealtimeEnabled(event.target.checked)}
                  />
                  <span>Real-time refresh</span>
                </label>

                <button
                  type="button"
                  className="brand-btn-secondary"
                  onClick={() =>
                    void queryClient.invalidateQueries({
                      queryKey: [
                        "copilot-artifacts",
                        selectedConnectionId,
                        "dashboard",
                      ],
                    })
                  }
                >
                  Refresh
                </button>

                <button
                  type="button"
                  className="brand-btn-secondary"
                  onClick={() => downloadCsv(activeDashboard, filteredCards)}
                >
                  Export CSV
                </button>

                <button
                  type="button"
                  className="brand-btn-secondary"
                  onClick={() => window.print()}
                >
                  Export PDF
                </button>
              </div>

              <div className="analytics-kpi-strip">
                {filteredCards.slice(0, 4).map((card, index) => (
                  <div key={`${activeDashboard.id}-kpi-${index}`}>
                    <span title={getCardTitle(card, index)}>
                      {getCardTitle(card, index)}
                    </span>
                    <strong>{formatNumber(getCardValue(card))}</strong>
                    <small title={getCardDescription(card)}>
                      {getCardDescription(card)}
                    </small>
                  </div>
                ))}
              </div>

              <div className="analytics-widget-grid">
                {orderedCards.length === 0 ? (
                  <div className="analytics-mini-empty">
                    No widgets match the current chart filter.
                  </div>
                ) : (
                  orderedCards.map(({ card, id, index, kind }) => (
                    <article
                      key={id}
                      className={`analytics-widget analytics-widget-${kind}`}
                      draggable
                      onDragStart={() => setDraggedWidgetId(id)}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={() => handleDropWidget(id)}
                    >
                      <div className="analytics-widget-head">
                        <div>
                          <span>Auto {kind}</span>
                          <h3 title={getCardTitle(card, index)}>
                            {getCardTitle(card, index)}
                          </h3>
                        </div>
                        <small>{formatNumber(getCardValue(card))}</small>
                      </div>
                      <p title={getCardDescription(card)}>{getCardDescription(card)}</p>
                      <AnalyticsChart card={card} kind={kind} />
                      <div className="analytics-widget-analysis">
                        <p title={getCardExplanation(card)}>
                          {getCardExplanation(card)}
                        </p>
                        <p title={getCardInterpretation(card)}>
                          {getCardInterpretation(card)}
                        </p>
                        <p title={getCardRecommendedAction(card)}>
                          {getCardRecommendedAction(card)}
                        </p>
                      </div>
                      {asText(card.sql) ? (
                        <details className="analytics-sql">
                          <summary>Evidence details</summary>
                          <pre>{asText(card.sql)}</pre>
                        </details>
                      ) : null}
                    </article>
                  ))
                )}
              </div>
            </>
          ) : (
            <div className="analytics-no-dashboard">
              <p className="page-kicker">Auto-Generated Dashboard</p>
              <h2>No dashboard selected</h2>
              <p>
                Create a dashboard and Vayent will profile the selected source,
                choose meaningful widgets, explain the business signal, and save
                the result.
              </p>
              <div className="analytics-quick-prompts">
                {quickPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => {
                      setDashboardPrompt(prompt);
                      handleCreateDashboard(prompt);
                    }}
                    disabled={
                      createDashboardMutation.isPending || !dashboardTokenAvailable
                    }
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}
        </main>
      </section>

      <section className="analytics-intelligence-grid">
        <div className="analytics-insight-panel">
          <div className="analytics-section-head">
            <div>
              <p className="page-kicker">Smart Insights</p>
              <h2>What Vayent noticed</h2>
            </div>
            <span className="brand-badge">{domain.label}</span>
          </div>

          <div className="analytics-insight-list">
            {insights.map((insight) => (
              <article
                key={`${insight.title}-${insight.body}`}
                className={`analytics-insight analytics-insight-${insight.tone}`}
              >
                <strong title={insight.title}>{insight.title}</strong>
                <p title={insight.body}>{insight.body}</p>
              </article>
            ))}
          </div>
        </div>

        <div className="analytics-insight-panel">
          <div className="analytics-section-head">
            <div>
              <p className="page-kicker">Recommendations</p>
              <h2>Next best actions</h2>
            </div>
            <span className="brand-badge">{domain.confidence}% confidence</span>
          </div>

          <div className="analytics-recommendation-list">
            {recommendations.map((recommendation) => (
              <article key={recommendation.title} className="analytics-recommendation">
                <span>{recommendation.priority}</span>
                <div>
                  <strong title={recommendation.title}>{recommendation.title}</strong>
                  <p title={recommendation.body}>{recommendation.body}</p>
                </div>
              </article>
            ))}
          </div>
        </div>

        <div className="analytics-insight-panel analytics-schema-panel">
          <div className="analytics-section-head">
            <div>
              <p className="page-kicker">Source Details</p>
              <h2>
                {selectedSourceIsDatabase
                  ? "Relationships and entities"
                  : "Sheets and fields"}
              </h2>
            </div>
            <Link
              to={
                selectedSourceIsDatabase && selectedConnectionId
                  ? `/connections/${selectedConnectionId}/schema`
                  : "/connections"
              }
              className="brand-btn-secondary"
            >
              {selectedSourceIsDatabase ? "Open schema" : "Manage source"}
            </Link>
          </div>

          <div className="analytics-relationship-list">
            {selectedSourceIsDatabase
              ? (schema?.relationships ?? []).slice(0, 6).map((relationship) => (
                  <span key={relationship.id}>
                    {getTableDisplayName(schema, relationship.source_table_name)}
                    {" / "}
                    {getColumnDisplayName(
                      schema,
                      relationship.source_table_name,
                      relationship.source_column_name,
                    )}
                    {" -> "}
                    {getTableDisplayName(schema, relationship.target_table_name)}
                    {" / "}
                    {getColumnDisplayName(
                      schema,
                      relationship.target_table_name,
                      relationship.target_column_name,
                    )}
                  </span>
                ))
              : sourceTables.slice(0, 6).map((table, index) => (
                  <span
                    key={`${getProfileTableName(table, `Sheet ${index + 1}`)}-detail`}
                    title={getProfileTableName(table, `Sheet ${index + 1}`)}
                  >
                    {getProfileTableName(table, `Sheet ${index + 1}`)}
                    {" / "}
                    {getProfileTableColumns(table)
                      .slice(0, 4)
                      .map((column) => humanizeIdentifier(asText(column.name, "Field")))
                      .join(", ")}
                  </span>
                ))}
            {selectedSourceIsDatabase && schema && schema.relationships.length === 0 ? (
              <span>No declared relationships detected.</span>
            ) : null}
            {selectedSourceIsDatabase && !schema ? (
              <span>Sync the source to reveal relationships.</span>
            ) : null}
            {!selectedSourceIsDatabase && sourceTables.length === 0 ? (
              <span>Refresh the spreadsheet to rebuild its profile.</span>
            ) : null}
          </div>
        </div>
      </section>

      <section className="analytics-copilot-builder">
        <div className="analytics-builder-mode">
          <button
            type="button"
            className={mode === "create" ? "is-active" : ""}
            onClick={() => setMode("create")}
          >
            New dashboard
          </button>
          <button
            type="button"
            className={mode === "modify" ? "is-active" : ""}
            onClick={() => setMode("modify")}
            disabled={!activeDashboard}
          >
            Revise current
          </button>
        </div>

        <input
          className="input"
          value={dashboardPrompt}
          onChange={(event) => setDashboardPrompt(event.target.value)}
          placeholder="Ask Vayent to build or revise a dashboard"
        />

        <button
          type="button"
          className="brand-btn-primary"
          onClick={() => handleCreateDashboard()}
          disabled={
            !selectedConnectionId ||
            createDashboardMutation.isPending ||
            !dashboardPrompt.trim() ||
            !dashboardTokenAvailable
          }
        >
          {createDashboardMutation.isPending ? "Generating..." : "Send to AI"}
        </button>
      </section>
    </div>
  );
};

export default DashboardPage;
