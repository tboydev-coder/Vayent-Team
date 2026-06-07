# Vayent User And Admin Guide

Vayent is an AI-assisted database workspace. It helps users connect PostgreSQL or MySQL databases, inspect schema metadata, ask natural-language questions, generate SQL, run safe queries, and review the execution trail.

## Who This Guide Is For

- **Users** use Vayent to connect databases, explore schemas, ask questions, and review query results.
- **Admins** manage users, usage, activity logs, notifications, support notes, token adjustments, and operational health.
- **Super admins** can also promote or demote admins and change feature flags.

## Getting Started

1. Open the Vayent web app.
2. Sign in with GitHub or Google.
3. Use the profile button in the top-right corner to review your account, plan, and remaining token allowance.
4. Add a database connection from **Connections**.
5. Sync the schema.
6. Use **Schema**, **Chat**, **Workspace**, **Copilot**, and **Logs** depending on the task.

## Account And Token Usage

The account menu shows:

- username and email
- free or paid plan label
- tokens used today
- tokens remaining today
- username edit form
- logout button

AI actions consume tokens. If you reach your limit, Vayent blocks new AI requests until the usage window resets or an admin adjusts your token balance.

## Connections

Use **Connections** to register live database sources.

Supported database types:

- PostgreSQL
- MySQL

To create a connection:

1. Enter a friendly connection name.
2. Choose PostgreSQL or MySQL.
3. Enter host, port, database name, username, and password.
4. Submit the form.

Vayent tests the connection, stores credentials encrypted, and syncs schema metadata. After a connection exists, you can:

- open a chat session for that database
- view the synced schema
- re-sync schema after database changes
- delete the connection

Best practice: re-sync schema after table, column, index, or relationship changes so AI answers use current metadata.

## Schema Explorer

Use **Schema** to inspect the synced structure of a connection.

The schema view shows:

- tables
- columns
- data types
- primary keys
- foreign keys
- nullable or required fields
- detected relationships
- an ERD-style schema map

You can add schema annotations:

- schema-level notes
- table descriptions
- column nicknames
- column descriptions

Annotations make AI results better because they give business meaning to raw database names. For example, you can explain that `mrr` means monthly recurring revenue or that `status = 3` means a cancelled account.

## Chat

Use **Chat** when you want to work with one database in a session-based conversation.

Good prompts are specific:

- "Show total revenue by month for the last six months."
- "Which customers have not logged in for 30 days?"
- "Find failed payments grouped by plan type."
- "Explain what this table is used for based on the schema."

Vayent may return:

- a generated SQL query
- query results
- a natural-language explanation
- a clarification if the request needs more context
- a confirmation request for destructive queries

Read-only queries can run immediately when they pass validation. Production deployments default to read-only connected-database access, so destructive queries such as `INSERT`, `UPDATE`, `DELETE`, or schema-changing statements are blocked unless an operator explicitly enables production writes. If writes are enabled, they still require explicit confirmation before execution.

## Workspace

Use **Workspace** when a question may involve more than one database.

Workspace lets you:

- select multiple connected databases
- choose one active database as fallback context
- ask cross-source questions
- compare trends or entities across selected sources
- keep recent workspace conversation context

Vayent routes the request to the best matching schema when one source is obvious. If the prompt asks for comparison or cross-source analysis, it can generate and run queries across multiple selected connections.

## Copilot

Use **Copilot** for deeper analysis and reusable business context.

Copilot features:

- **Investigations**: root-cause analysis using schema and data context.
- **Briefings**: executive summaries of product, customer, or operational signals.
- **Recommendations**: prioritized next actions.
- **Scenarios**: what-if analysis.
- **Dashboards**: AI-generated metric cards and evidence queries.
- **Memories**: saved business context, goals, definitions, or constraints.
- **Metric monitoring**: currently disabled for the live release and shown as a coming-soon workflow.

Tips:

- Save important definitions as memories before asking strategic questions.
- Use dashboards or investigations for metric review until monitoring is enabled.
- Keep prompts grounded in specific outcomes, time windows, and business terms.

## Logs

Use **Logs** to review query execution history.

Logs include:

- generated or executed SQL
- query type
- success or error status
- row count
- execution time
- database filter
- day-based grouping

Logs are useful for auditing AI behavior, debugging failed prompts, and reviewing what changed after destructive query confirmation when production writes are enabled.

## Admin Dashboard

Admins can open **Admin** from the sidebar after signing in with an admin account.

Admin dashboard sections:

- **Dashboard Overview**: total users, new users, active users, requests, AI generations, token usage, admins, retention, and error rate.
- **Users**: search users, inspect account status, view plan and token usage, suspend or reactivate users, and add support notes.
- **Analytics**: engagement trends, signup trends, API usage, AI usage, and retention signals.
- **AI Usage**: token consumption, top AI users, users close to limits, prompt volume, and model usage.
- **Tokens**: manually add or deduct token balance with an audit reason.
- **Activity Logs**: search/filter system activity and export CSV.
- **Customer Support**: save internal notes and review recent failed actions.
- **Admin Management**: view admins and, if super admin, change admin roles.
- **System Health**: database health, OpenAI configuration, active connections, uptime, error rate, and queue status.
- **Notifications**: acknowledge or resolve admin notifications.
- **Security Logs**: failed login attempts, unauthorized admin attempts, rate-limit activity, and suspended users.
- **Settings**: feature flags. Super admins can turn flags on/off.

Admin write actions use an additional server-side admin guard. The UI sends the required admin CSRF header automatically for supported actions.

## Admin Permissions

Admins can:

- view dashboard analytics
- list and search users
- suspend and reactivate users
- edit internal user notes
- adjust token balances
- view and export activity logs
- acknowledge and resolve notifications

Super admins can also:

- promote users to admin
- demote admins
- grant or remove super admin status
- update feature flags

Bootstrap admins are protected by backend rules. Use `ADMIN_BOOTSTRAP_EMAILS` in the backend environment to define trusted bootstrap admin emails.

## Admin Best Practices

- Always include a clear reason when adjusting tokens.
- Use suspension for abuse, compromised accounts, or serious policy issues.
- Review activity logs before changing roles.
- Export filtered activity logs when investigating production incidents.
- Keep feature flag changes small and deliberate.
- Confirm OAuth callback, cookie, and CORS settings after every production domain change.
- Use least-privilege database accounts for every connection. Prefer read-only accounts for production databases.

## Troubleshooting

- **Cannot sign in**: check that the OAuth provider is configured and callback URLs match the backend environment.
- **No database connection**: verify host, port, credentials, network access, and database permissions.
- **Schema is stale**: re-sync the connection from **Connections**.
- **AI features fail**: check token limit, OpenAI API key, and `/health`.
- **Admin link missing**: sign in as an admin or super admin.
- **Frontend cannot reach API**: verify `VITE_API_BASE_URL` was set before building the frontend.
