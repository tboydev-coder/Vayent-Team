import React, { useState } from "react";
import { Link } from "react-router-dom";

import BrandLogo from "../components/BrandLogo";
import DatabaseOrbitScene from "../components/DatabaseOrbitScene";
import { TrustCenterLinks } from "../components/TrustCenterModal";
import "../styles/landing.css";

const trustSignals = [
  "Encrypted database credentials",
  "Destructive query confirmation",
  "Schema-aware SQL generation",
  "Execution logs and audit trail",
  "PostgreSQL and MySQL support",
  "Multi-database workspace",
];

const featureCards = [
  {
    label: "AI Chat",
    title: "Ask business questions in plain English.",
    description:
      "Get SQL, explanations, summaries, and trends without starting from a blank query editor.",
    preview: "Which customers stopped paying last month?",
  },
  {
    label: "Schema Explorer",
    title: "Understand tables, columns, keys, and relationships.",
    description:
      "Visual schema maps and annotations help Vayent reason with your real database context.",
    preview: "orders -> customers -> subscriptions",
  },
  {
    label: "SQL Generation",
    title: "Generate safer SQL with validation and review.",
    description:
      "Vayent drafts schema-aware SQL, validates risk, and asks for confirmation before destructive execution.",
    preview: "SELECT plan, SUM(mrr) FROM subscriptions...",
  },
  {
    label: "Workspace",
    title: "Investigate across multiple databases.",
    description:
      "Route questions to one source or compare signals across selected PostgreSQL and MySQL systems.",
    preview: "Compare churn in production and billing data.",
  },
  {
    label: "Copilot",
    title: "Move from query answers to root-cause analysis.",
    description:
      "Run investigations, briefings, recommendations, scenarios, dashboards, and saved business context.",
    preview: "Why did revenue drop last week?",
  },
  {
    label: "Audit Trail",
    title: "Keep AI transparent and accountable.",
    description:
      "Track generated SQL, row counts, execution time, failures, confirmations, and user activity.",
    preview: "success - 248 rows - 312ms",
  },
  {
    label: "Memory",
    title: "Teach Vayent how your business works.",
    description:
      "Save KPI definitions, product terms, operating rules, and internal context so answers improve over time.",
    preview: "MRR excludes trials and internal accounts.",
  },
];

const showcaseTabs = [
  {
    id: "connect",
    label: "Connect Database",
    title: "Register live sources with encrypted credentials.",
    detail:
      "Add PostgreSQL or MySQL, verify access, and keep each source available for chat, schema, workspace, and copilot flows.",
    query: "postgres://growth-prod",
    result: ["Connection verified", "Credentials encrypted", "Source ready"],
  },
  {
    id: "schema",
    label: "Sync Schema",
    title: "Turn raw metadata into AI-ready context.",
    detail:
      "Vayent maps tables, columns, keys, relationships, row hints, and business annotations before generation starts.",
    query: "sync public schema",
    result: [
      "42 tables indexed",
      "318 columns mapped",
      "19 relationships found",
    ],
  },
  {
    id: "ask",
    label: "Ask AI",
    title: "Ask for outcomes, not syntax.",
    detail:
      "The assistant decides whether to explain, clarify, generate SQL, or run a safe read query against the selected context.",
    query: "Why did active subscriptions fall last week?",
    result: [
      "Segmented by plan",
      "Billing failures isolated",
      "Churn cohort detected",
    ],
  },
  {
    id: "sql",
    label: "Generate SQL",
    title: "Review generated SQL before it touches data.",
    detail:
      "Generated SQL is grounded in schema context and checked for risk before execution. Destructive work requires confirmation.",
    query:
      "SELECT plan, COUNT(*) FROM subscriptions WHERE status='churned' GROUP BY plan;",
    result: ["Read-only query", "Validated", "Ready to execute"],
  },
  {
    id: "results",
    label: "Review Results",
    title: "Move from rows to decisions.",
    detail:
      "Vayent returns rows, summaries, warnings, and natural language explanations that help teams decide what to do next.",
    query: "show top affected customer segments",
    result: ["Enterprise: -8.4%", "Startup: -2.1%", "Self serve: +1.8%"],
  },
  {
    id: "investigate",
    label: "Investigate Metrics",
    title: "Launch deeper investigations from one prompt.",
    detail:
      "Copilot pulls evidence queries, likely causes, affected segments, and next actions into a saved artifact.",
    query: "Investigate revenue drop after the release.",
    result: [
      "Cause: checkout errors",
      "Affected: EU monthly plans",
      "Action: rollback flag",
    ],
  },
];

const investigationCards = [
  {
    title: "Revenue Investigation",
    prompt: "Why did subscription revenue drop last week?",
    findings: [
      "Billing retry failures rose 31%",
      "Enterprise downgrades clustered in EU",
      "Two plans account for 72% of the loss",
    ],
  },
  {
    title: "Customer Churn Analysis",
    prompt: "Which customers are most likely to churn?",
    findings: [
      "Login frequency fell below baseline",
      "Support tickets increased",
      "Seats dropped before renewal",
    ],
  },
  {
    title: "Product Usage Analysis",
    prompt: "What changed after the latest release?",
    findings: [
      "Activation improved for new teams",
      "Report exports slowed",
      "Mobile sessions dipped 6%",
    ],
  },
  {
    title: "Failed Payments Investigation",
    prompt: "Why are payment failures increasing?",
    findings: [
      "Card expiry failures doubled",
      "Gateway latency spiked",
      "Retry success fell after midnight UTC",
    ],
  },
];

const useCases = [
  {
    audience: "Developers",
    points: [
      "Schema-aware SQL",
      "Query debugging",
      "Execution transparency",
      "Faster internal tools",
    ],
  },
  {
    audience: "Startups",
    points: [
      "Growth monitoring",
      "Churn investigation",
      "Customer behavior",
      "Operational signals",
    ],
  },
  {
    audience: "Analysts",
    points: [
      "Natural language analytics",
      "Dashboard generation",
      "Root-cause workflows",
      "Evidence queries",
    ],
  },
  {
    audience: "Product Teams",
    points: [
      "Feature impact analysis",
      "Retention tracking",
      "User segmentation",
      "Release monitoring",
    ],
  },
  {
    audience: "SQL Learners",
    points: [
      "See SQL from questions",
      "Learn schema relationships",
      "Compare prompt and query",
      "Build confidence safely",
    ],
  },
];

const dashboardMetrics = [
  ["Revenue", "$182K", "+12.8%"],
  ["Active users", "48.2K", "+8.1%"],
  ["AI requests", "31.4K", "99.2% success"],
  ["Query volume", "12.8K", "312ms avg"],
  ["Token usage", "4.8M", "18% below cap"],
  ["DB health", "Healthy", "6 sources"],
];

const LandingPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState(showcaseTabs[2]);

  return (
    <div className="landing-page">
      <nav className="landing-nav" aria-label="Landing navigation">
        <Link to="/" className="landing-brand">
          <BrandLogo className="landing-brand-logo" />
          <span>Vayent</span>
        </Link>
        <div className="landing-nav-links">
          <a href="#platform">Platform</a>
          <a href="#showcase">Workflow</a>
          <a href="#security">Security</a>
          <TrustCenterLinks
            className="landing-trust-links"
            sections={["privacy", "security"]}
          />
          <Link to="/login">Log in</Link>
        </div>
      </nav>

      <header className="landing-hero">
        <div className="landing-hero-visual" aria-hidden="true">
          <DatabaseOrbitScene className="landing-database-scene" />
          <div className="hero-product-shell">
            <div className="hero-product-sidebar">
              <span />
              <span />
              <span />
              <span />
            </div>
            <div className="hero-product-main">
              <div className="hero-product-topline">
                <span>Workspace live</span>
                <strong>6 databases</strong>
              </div>
              <div className="hero-product-grid">
                <div className="hero-chat-panel">
                  <p>AI Investigation</p>
                  <h3>Why did revenue fall last week?</h3>
                  <div className="hero-chat-line hero-chat-line-user" />
                  <div className="hero-chat-line hero-chat-line-ai" />
                  <div className="hero-chat-line hero-chat-line-ai short" />
                </div>
                <div className="hero-sql-panel">
                  <p>Generated SQL</p>
                  <pre>{`SELECT plan, SUM(mrr)
FROM subscriptions
WHERE churned_at >= NOW() - INTERVAL '7 days'
GROUP BY plan;`}</pre>
                </div>
                <div className="hero-chart-panel">
                  <p>Revenue signals</p>
                  <div className="hero-bars">
                    <span style={{ height: "46%" }} />
                    <span style={{ height: "62%" }} />
                    <span style={{ height: "78%" }} />
                    <span style={{ height: "54%" }} />
                    <span style={{ height: "88%" }} />
                    <span style={{ height: "68%" }} />
                  </div>
                </div>
                <div className="hero-alert-panel">
                  <p>Query review</p>
                  <strong>Validated before execution</strong>
                  <span>Audit trail attached</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="landing-hero-copy">
          <p className="landing-kicker">AI database intelligence</p>
          <h1>Vayent</h1>
          <br />
          <h3>Talk to your database like it understands your business.</h3>
          <p>
            Connect PostgreSQL and MySQL, sync schema context, ask questions in
            natural language, generate safer SQL, investigate trends, and
            monitor execution with full traceability.
          </p>
          <div className="landing-hero-actions">
            <Link to="/login" className="landing-btn landing-btn-primary">
              Start with OAuth
            </Link>
            <a href="#showcase" className="landing-btn landing-btn-secondary">
              Explore Workspace
            </a>
          </div>
        </div>
      </header>

      <main>
        <section className="landing-trust" aria-label="Trust signals">
          {trustSignals.map((signal) => (
            <span key={signal}>{signal}</span>
          ))}
        </section>

        <section
          id="platform"
          className="landing-section landing-feature-section"
        >
          <div className="landing-section-head">
            <p className="landing-kicker">Everything you need</p>
            <h2>
              An AI workspace for queries, schemas, metrics, and decisions.
            </h2>
            <p>
              Vayent combines a SQL copilot, data analyst, investigation engine,
              and operations console in one workspace built around your schema.
            </p>
          </div>
          <div className="landing-feature-grid">
            {featureCards.map((feature) => (
              <article className="landing-feature" key={feature.label}>
                <span>{feature.label}</span>
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
                <div className="landing-feature-preview">{feature.preview}</div>
              </article>
            ))}
          </div>
        </section>

        <section id="showcase" className="landing-section landing-showcase">
          <div className="landing-section-head">
            <p className="landing-kicker">Interactive workflow</p>
            <h2>From database connection to business evidence in seconds.</h2>
          </div>

          <div className="landing-showcase-grid">
            <div className="landing-showcase-tabs">
              {showcaseTabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={activeTab.id === tab.id ? "is-active" : ""}
                  onClick={() => setActiveTab(tab)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="landing-showcase-stage">
              <div>
                <p className="landing-kicker">{activeTab.label}</p>
                <h3>{activeTab.title}</h3>
                <p>{activeTab.detail}</p>
              </div>
              <div className="landing-terminal">
                <span>vayent://workspace</span>
                <pre>{activeTab.query}</pre>
              </div>
              <div className="landing-result-grid">
                {activeTab.result.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="landing-section landing-investigations">
          <div className="landing-section-head">
            <p className="landing-kicker">Go beyond dashboards</p>
            <h2>
              Use AI investigations when the business needs an answer, not
              another chart.
            </h2>
          </div>
          <div className="landing-investigation-grid">
            {investigationCards.map((card) => (
              <article className="landing-investigation" key={card.title}>
                <p>{card.title}</p>
                <h3>{card.prompt}</h3>
                <ul>
                  {card.findings.map((finding) => (
                    <li key={finding}>{finding}</li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section landing-dashboard-preview">
          <div className="landing-section-head">
            <p className="landing-kicker">Smart dashboard preview</p>
            <h2>Operational intelligence for teams that move fast.</h2>
          </div>
          <div className="landing-dashboard-shell">
            <div className="landing-dashboard-head">
              <div>
                <span>Workspace preview</span>
                <h3>Growth, reliability, and AI usage</h3>
              </div>
              <strong>Live</strong>
            </div>
            <div className="landing-dashboard-metrics">
              {dashboardMetrics.map(([label, value, delta]) => (
                <div className="landing-dashboard-metric" key={label}>
                  <p>{label}</p>
                  <strong>{value}</strong>
                  <span>{delta}</span>
                </div>
              ))}
            </div>
            <div className="landing-dashboard-bottom">
              <div className="landing-timeline">
                <span style={{ width: "40%" }} />
                <span style={{ width: "72%" }} />
                <span style={{ width: "58%" }} />
                <span style={{ width: "86%" }} />
              </div>
              <div className="landing-ai-panel">
                <p>AI insight</p>
                <span>
                  Query failures are concentrated in billing exports. Retry
                  latency rose after the latest gateway change.
                </span>
              </div>
            </div>
          </div>
        </section>

        <section className="landing-section landing-use-cases">
          <div className="landing-section-head">
            <p className="landing-kicker">Built for modern teams</p>
            <h2>
              One workspace for builders, analysts, startups, and product teams.
            </h2>
          </div>
          <div className="landing-use-case-grid">
            {useCases.map((useCase) => (
              <article className="landing-use-case" key={useCase.audience}>
                <h3>{useCase.audience}</h3>
                <ul>
                  {useCase.points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>

        <section id="security" className="landing-section landing-split">
          <div>
            <p className="landing-kicker">Production-grade safeguards</p>
            <h2>Trust the process behind every AI-generated query.</h2>
            <p>
              Vayent is designed around encrypted credentials, OAuth
              authentication, role-based permissions, audit logs, execution
              history, query validation, and confirmation for destructive
              queries.
            </p>
          </div>
          <div className="landing-security-list">
            {[
              "Encrypted credentials",
              "OAuth login",
              "Admin permissions",
              "Activity logs",
              "Query validation",
              "Execution history",
            ].map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </section>

        <section className="landing-section landing-intelligence">
          <div className="landing-section-head">
            <p className="landing-kicker">Performance and intelligence</p>
            <h2>Serious architecture for schema-aware reasoning.</h2>
          </div>
          <div className="landing-intelligence-grid">
            {[
              "Fast schema sync",
              "RAG-backed schema context",
              "Cross-database routing",
              "Execution tracing",
              "Query validation",
              "Operational analytics",
              "Evidence summaries",
              "Token metering",
            ].map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </section>

        <section className="landing-final-cta">
          <p className="landing-kicker">
            Your database already has the answers
          </p>
          <h2>Vayent helps you find them.</h2>
          <p>
            Stop writing complex SQL from scratch. Start asking sharper
            questions, investigating faster, and building confidence in every
            data decision.
          </p>
          <div className="landing-hero-actions">
            <Link to="/login" className="landing-btn landing-btn-primary">
              Start with OAuth
            </Link>
            <Link to="/login" className="landing-btn landing-btn-secondary">
              Connect Your Database
            </Link>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <Link to="/" className="landing-brand">
          <BrandLogo className="landing-brand-logo" />
          <span>Vayent</span>
        </Link>
        <div>
          <a href="#platform">Platform</a>
          <a href="#showcase">Workflow</a>
          <a href="#security">Security</a>
          <TrustCenterLinks
            className="landing-footer-trust-links"
            sections={["privacy", "security", "terms"]}
          />
          <Link to="/login">Sign in</Link>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
