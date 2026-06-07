export type TrustCenterSectionId = "privacy" | "security" | "terms";

export type TrustCenterGroup = {
  title: string;
  body?: string;
  items?: string[];
};

export type TrustCenterSection = {
  id: TrustCenterSectionId;
  label: string;
  title: string;
  summary: string;
  groups: TrustCenterGroup[];
};

export const TRUST_CENTER_UPDATED_AT = "May 8, 2026";

export const TRUST_CENTER_SECTIONS: TrustCenterSection[] = [
  {
    id: "privacy",
    label: "Privacy",
    title: "Privacy Policy",
    summary:
      "Vayent is designed for teams that connect sensitive business databases. This policy explains what the app collects, how database data is used, who it may be shared with, and what choices users and customers have.",
    groups: [
      {
        title: "The access boundary",
        body:
          "Vayent team members do not log into, browse, export, or manually query your connected source databases as part of normal operations. Database credentials are encrypted in the application database and are used by the Vayent application service only to perform actions that an authenticated workspace user starts or schedules.",
        items: [
          "Vayent teams do not receive your raw database username or password through the product UI.",
          "Vayent teams do not use your connected database for training, demos, sales, analytics, or support without your explicit written permission.",
          "Support should use screenshots, redacted logs, generated SQL, or customer-provided samples instead of live database access.",
        ],
      },
      {
        title: "Information collected",
        items: [
          "Account information such as username, email address, OAuth provider, sign-in timestamps, role, plan type, and token usage.",
          "Workspace metadata such as connection names, database type, host, port, database name, schema sync status, table names, column names, relationships, annotations, saved memories, watchlists, and dashboard artifacts.",
          "Chat and copilot content such as prompts, generated SQL, AI explanations, result summaries, selected query rows saved in chat history, warnings, and execution status.",
          "Operational records such as activity logs, query logs, row counts, execution timing, errors, health checks, and admin actions.",
          "Notification and support data needed to send lifecycle emails, respond to support requests, and investigate security or reliability issues.",
          "Billing metadata such as plan, usage limits, invoice identifiers, and payment-provider customer identifiers when paid billing is enabled.",
        ],
      },
      {
        title: "Source database data",
        body:
          "Vayent connects to your database only through the credentials and network access that you configure. The app may read schema metadata, execute generated or confirmed SQL, store query history, and save AI-generated summaries so that users can review their work later.",
        items: [
          "Schema sync reads metadata so Vayent can understand tables, columns, keys, and relationships.",
          "Read queries may store returned rows in chat history when the answer needs result evidence.",
          "Production deployments block write or destructive SQL by default. If an operator explicitly enables writes, destructive SQL still requires user confirmation before execution.",
          "Sensitive result fields are filtered in chat display paths, but customers should still avoid connecting overly privileged database accounts or unnecessary regulated data.",
          "Customers remain responsible for the permissions, retention, and legal basis for personal data that exists in connected source databases.",
        ],
      },
      {
        title: "AI processing",
        body:
          "AI features may send prompts, schema context, generated SQL, relevant query results, summaries, and system instructions to the configured AI provider. Provider handling depends on the deployed account, region, retention controls, and service terms.",
        items: [
          "Vayent does not sell customer prompts, schema metadata, query results, or company data.",
          "Vayent does not intentionally use customer database content to train Vayent-owned models.",
          "For OpenAI API deployments, admins should review OpenAI's API data controls, retention settings, and opt-in training settings before production use.",
          "Customers should not submit secrets, private keys, raw passwords, or unnecessary regulated data in prompts.",
          "If an organization needs stricter isolation, use a dedicated production deployment, approved AI provider settings, and a written data processing agreement.",
        ],
      },
      {
        title: "Legal basis, rights, and choices",
        body:
          "The deploying organization should identify its legal entity, support contact, data protection contact, lawful bases, and region-specific notices before public launch.",
        items: [
          "Users may request access, correction, deletion, export, restriction, objection, or other rights available under applicable privacy laws through the configured support channel.",
          "Customers can delete connections and rotate source database credentials; workspace admins should define how long logs, prompts, query results, and backups are retained.",
          "Vayent does not sell personal information. If a deployment uses advertising, analytics, or cross-context sharing, the operator must update this policy and provide any required opt-out choices.",
          "Material privacy changes should be communicated before new data uses begin when required by law, contract, or internal policy.",
        ],
      },
      {
        title: "Retention, deletion, and portability",
        items: [
          "Workspace records are retained until deleted by the user, admin, configured retention policy, hosting operator, or applicable contract.",
          "Backups may retain deleted data for a limited operational recovery period.",
          "Admins should define retention windows for chat history, query logs, activity logs, schema snapshots, copilot artifacts, support data, and backups before production launch.",
          "Users may request account deletion or export through their workspace admin or the configured support channel.",
        ],
      },
    ],
  },
  {
    id: "security",
    label: "Security",
    title: "Security Policy",
    summary:
      "Vayent combines OAuth, encrypted credentials, query safety controls, audit trails, and operational limits to reduce risk when teams work with live databases.",
    groups: [
      {
        title: "Core safeguards",
        items: [
          "OAuth sign-in is used so Vayent does not store user passwords.",
          "Refresh cookies should be secure, scoped, and HTTPS-only in production.",
          "Database credentials are encrypted before storage and decrypted only by backend services when a user action requires a database operation.",
          "Production source-database connections require TLS when configured with the provided production template.",
          "Connected databases are read-only by default in production, and production writes require an explicit operator acknowledgement before they can be enabled.",
          "Local, metadata-service, link-local, and other blocked database hosts are rejected unless the deployment explicitly allows private hosts for a trusted private network.",
          "Admin and super-admin roles control administrative features such as user management, token adjustments, feature flags, and operational visibility.",
          "Token metering and rate limiting help control AI usage and reduce abuse.",
          "Generated SQL is validated before execution; unsafe patterns, row-locking reads, file-access functions, multi-statement SQL, and destructive SQL are blocked by policy.",
          "Query logs, activity logs, execution status, row counts, and error records support auditing and incident review.",
        ],
      },
      {
        title: "Customer responsibilities",
        items: [
          "Use a least-privilege database user for each connection. Prefer read-only access unless write operations are required.",
          "Restrict source database network access to the Vayent backend host or private network whenever possible.",
          "Rotate database credentials after suspected exposure, staff changes, or access policy changes.",
          "Review generated SQL before running it, especially for updates, deletes, schema changes, or large exports.",
          "Do not connect production databases before HTTPS, secret management, backups, monitoring, and access controls are ready.",
          "Avoid placing regulated data in prompts unless your legal, security, and compliance requirements have been approved.",
        ],
      },
      {
        title: "Production configuration",
        items: [
          "Set APP_ENV to production, disable DEBUG, disable API_DOCS_ENABLED, disable AUTO_CREATE_TABLES, and use a managed production database.",
          "Store SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, OAuth secrets, SMTP secrets, OpenAI keys, and database URLs in a secret manager, not in git.",
          "Set strict TRUSTED_HOSTS, ALLOWED_ORIGINS, production OAuth callback URLs, and secure refresh-cookie settings.",
          "Run migrations before production startup; runtime schema patching is disabled when AUTO_CREATE_TABLES is false.",
          "Monitor /health, database reachability, OpenAI reachability, failed logins, failed requests, and error rates.",
          "Keep backups, restore tests, dependency updates, and incident runbooks current.",
        ],
      },
      {
        title: "Incident response",
        items: [
          "If database access is suspected, disable the affected connection and rotate the source database credentials.",
          "Review activity logs, query logs, generated SQL, and admin actions for the affected window.",
          "Invalidate active sessions if account access is suspected.",
          "Notify affected customers or users when required by contract, law, or internal policy.",
          "Preserve relevant logs for investigation while limiting unnecessary exposure of personal or company data.",
        ],
      },
    ],
  },
  {
    id: "terms",
    label: "Terms",
    title: "Acceptable Use and Product Terms",
    summary:
      "These terms set expectations for safe use of Vayent with live databases, AI-generated SQL, and company information.",
    groups: [
      {
        title: "Authorized use",
        items: [
          "Only connect databases that you own, administer, or are explicitly authorized to access.",
          "Use least-privilege credentials and prefer read-only database users for production data.",
          "Do not use Vayent to bypass access controls, extract data without permission, or run queries for unlawful purposes.",
          "Do not upload malware, private keys, raw passwords, or data that your organization is not permitted to process in Vayent.",
          "Admins are responsible for inviting the right users, assigning roles, and removing access when it is no longer needed.",
        ],
      },
      {
        title: "AI and SQL responsibility",
        body:
          "AI output can be incomplete, wrong, outdated, or unsafe for a specific business context. Users remain responsible for reviewing prompts, generated SQL, query results, and recommendations before acting on them.",
        items: [
          "Review generated SQL before execution.",
          "Keep production writes disabled unless the organization has approved backups, restore testing, audit review, and rollback procedures.",
          "Use confirmation prompts carefully for writes, deletes, and schema changes when writes are enabled.",
          "Validate business recommendations against source records and human judgment.",
          "Do not treat Vayent output as legal, financial, medical, tax, or compliance advice.",
        ],
      },
      {
        title: "Production write override",
        body:
          "Production write capability is an operator-controlled exception, not the default product posture.",
        items: [
          "Operators must intentionally set the write-policy environment variables before destructive SQL can run in production.",
          "A confirmation token is scoped to the signed-in user and selected database connection.",
          "Customers remain responsible for source database backups, approval workflows, and recovery from user-confirmed SQL changes.",
        ],
      },
      {
        title: "Service operations",
        items: [
          "Vayent may limit, suspend, or block usage that threatens reliability, security, or legal compliance.",
          "Availability can be affected by hosting providers, databases, OAuth providers, SMTP providers, and AI providers.",
          "Admins should maintain their own backups, monitoring, incident process, and source database access controls.",
          "Policy updates should be reflected in the product, reviewed for the deployment's jurisdiction, and communicated to affected users when material.",
        ],
      },
    ],
  },
];

export const getTrustCenterSection = (
  sectionId: TrustCenterSectionId,
): TrustCenterSection =>
  TRUST_CENTER_SECTIONS.find((section) => section.id === sectionId) ??
  TRUST_CENTER_SECTIONS[0];
