# TechHub Delivery Workflow

A comprehensive order fulfillment and delivery management system for Texas A&M University's TechHub. This application manages the complete lifecycle of hardware orders from inventory picking through delivery, signature capture, and final fulfillment.

---

## Table of Contents

- [Overview](#overview)
- [Using the App](#using-the-app)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Development and Testing](#development-and-testing)
- [Security](#security)
- [Documentation](#documentation)
- [Scripts](#scripts)

---

## Overview

The TechHub Delivery Workflow App streamlines order delivery operations by integrating with Inflow inventory management, Microsoft 365 services, and ArcGIS location services. It replaces fragmented scripts and manual processes with a unified, auditable system.

### Key Capabilities

| Category | Features |
|----------|----------|
| **Order Management** | Inflow sync (polling + webhooks), partial-order legs, location extraction, order remarks parsing |
| **Delivery & Shipping** | Delivery runs with vehicle checkout, pickup runs, and shipment handoff tracking |
| **Preparation & QA** | Asset-tag requests, batch picklists, QA checklists, workflow gates |
| **Documents** | PDF generation, workstation printing, in-app signature capture |
| **Notifications** | Email via Graph API, Teams via SharePoint queue + Power Automate |
| **Real-time** | WebSocket updates for live delivery tracking |
| **Catalog Tools** | Product vetting, computer/dock compatibility editing, inventory reorder reports, product comparison |
| **Audit & Security** | Audit and status history, Microsoft Entra OIDC with SAML fallback, access controls, session management |

---

## Using the App

This section is for staff using an already deployed app. Use the address supplied by your team; the localhost addresses and [Quick Start](#quick-start) below are for developers running their own copy.

### Sign In and Find Your Work

1. Open the app and select **Sign in with NetID**. Use your own work account so tagging, QA, vehicle checkout, and delivery actions are attributed correctly.
2. If access is denied, ask an app admin to check your access allowlist. Operator access and admin access are separate; some tools are visible only to admins.
3. Open **Orders**, search for the order number, and open its details. Check its current status, recipient/location, assigned items, and any issue reason before acting. Partial-order parts and the original remainder are separate rows.
4. Use the sidebar to move to the queue for the next task. On a small screen, use the **Open sidebar** button.

| Screen | When to use it |
|--------|----------------|
| **Orders** | Find an order, inspect items and documents, check status/history, or record an issue |
| **Preparation** | Send tag requests, record applied tags, and generate documents for eligible picked orders |
| **QA Checklist** | Review orders awaiting QA and submit their checklists |
| **Delivery** | Select Pre-Delivery orders, start delivery or pickup runs, sign documents, and complete runs |
| **Shipping** | Find orders routed to shipping and record carrier handoff |
| **Admin Tools** | Admin diagnostics, integration controls, and workflow settings |

### Prepare an Order

1. Open **Preparation**. In **Tag Request Actions**, select the eligible orders needing tags. Review the selection, then choose **Upload orders** and confirm **Upload now** to send the request to Canopy. This uploads data and may also notify Teams.
2. After the tags have actually been applied, select the orders and choose **Mark selected as tagged**, then confirm. Sending a tag request alone does not mean tagging is finished.
3. In **Generate Picklist & Order Details**, select the ready orders and choose **Generate**, then **Generate now**. For an individual order, its detail page also offers document generation.
4. Review the result for each order, including blocked or failed entries. Confirm that the documents were generated and the order moved to **QA**. Generation can send the order-details email and queue physical printing when enabled; check the result before repeating it.

If a partial-order confirmation appears, review the picked and missing quantities before selecting **Create Part**. For example, if two of three devices are picked, the picked part proceeds through preparation, QA, and fulfillment while the original row retains the remaining device. Work on the part's documents for that delivery. When the remaining device is picked and synced, prepare the original remainder through its own tagging, document, and QA cycle.

### Complete QA

1. Open **QA Checklist**, find the order under **Orders Needing QA**, and select **Perform QA**. Use **Edit QA** when reviewing an existing submission.
2. Verify the physical items against every checklist requirement: asset tags and serials, customer documents, packaging, packing slip, and box labels. Check each item only after verifying it.
3. Review the displayed delivery/shipping routing. The app derives routing from the order; the checklist is not a manual routing selector. Ask an admin to investigate unexpected routing before proceeding.
4. Select **Submit QA Checklist**. After a successful submission, delivery orders move to **Pre-Delivery** and shipping orders move to **Shipping**.

If **QA unavailable** appears because you picked the order, another signed-in person must perform QA when the separate-reviewer rule is enabled. A remainder with items still waiting to be picked cannot complete QA; once its remaining items and preparation are ready, it can proceed separately.

### Deliver or Hand Over a Pickup

1. Open **Delivery** and select ready orders from **Pre-Delivery Orders**. Review the selected list and arrange the stop sequence in **Dispatch Order**.
2. Choose the dispatch target and purpose offered by the action bar. For vehicle delivery, choose an available vehicle and a delivery purpose, then use the start action. The app records the current user as runner and checks out an available vehicle as part of starting the run. **Pickup** uses **Start Pickup** without a vehicle checkout.
3. Open the run under **Active Delivery**. Confirm the recipient and items at each handoff, then select the order's **Sign Document** action.
4. On **Sign Delivery Document**, choose **Add Signature**, capture the recipient's signature, and position it on the PDF. Select **Finish & Save** and wait for success. Saving the signed document marks the order **Delivered**; drawing or placing a signature alone does not save it. Use **Use Last Signature** only for the same recipient's authorized handoff when the app offers it.
5. After all remaining orders show **Delivered**, select **Complete Run**. This also attempts fulfillment in Inflow. If it fails, inspect the reported error and confirm the run's state before trying again.
6. Return a used vehicle and select **Check In** on Delivery. Run completion and vehicle check-in are separate actions; confirm both are recorded.

For an undeliverable order, use the run's recall action and enter the reason. Recall marks the order **Issue** and removes it from the active run so the other deliveries can finish. For other problems, the order detail status menu offers **Raise Issue**; record what needs resolving instead of marking an incomplete handoff delivered.

### Ship an Order

1. Complete QA with shipping routing, then open **Shipping**. The **Shipment Queue** contains orders whose overall status is Shipping.
2. Open the order to verify its items and destination, and complete the physical packaging and carrier handoff using your team's shipping process.
3. At carrier handoff, select **Mark Shipped** on the queue row. The current queue allows this from Work Area or Dock and does not prompt for a carrier name or tracking number.
4. Confirm the order now shows **Delivered** in Orders. It leaves the Shipment Queue because its overall status changed. Here, Delivered means handed to the carrier, not confirmed arrival at the recipient.

### Use the Catalog Tools

| Task | Steps and result |
|------|------------------|
| **Vetting Editor** (admin) | Open the editor, add or update product entries, complete required names and URLs, then select **Save**. Saving writes the remote vetting data. |
| **Compatibility Editor** | Open the computer/dock matrix and record changes. Contributions may require submission and admin review. An admin uses **Save to WebDAV** to publish the approved snapshot; database edits and reviews alone do not publish it. |
| **Inventory Reorder** (admin) | Review the saved summary and its refresh time, search/filter the rows, and download the report as needed. Use refresh to fetch current data and wait for completion. Use the report to guide purchasing; refreshing it does not place a purchase order. |
| **Product Checker** (admin) | Review the saved scan or start a fresh scan, then inspect categories and source-record links. Download JSON/text for the full report. A scan reports differences without correcting catalog records; read each category's scope before interpreting a match or gap. |

### When You Cannot Proceed

| What you see | What to check next |
|--------------|--------------------|
| An order is missing from a queue | Search in **Orders** and clear filters. Preparation, QA, Delivery, and Shipping show different eligible states. Check whether the work belongs to a partial child row. If the order has not synced, ask an admin to inspect sync health. |
| Picklist generation is blocked | Check required tags, picker identity rules, whether a picklist already exists, and whether the remainder has new picked items. Read the per-order failure message before retrying. |
| QA submission is disabled or rejected | Complete all checklist items, check the separate-reviewer rule, and confirm the order's documents and picked quantities are ready. |
| A vehicle/start action is unavailable | Select orders and the required target/purpose. Check for another user's checkout or an active run. A vehicle checked out for Other must be checked in before delivery use. |
| Complete Run is unavailable or fails | Check remaining order statuses. Sign completed handoffs or recall undeliverable orders with a reason. If all are delivered, inspect the error for an Inflow fulfillment failure. |
| “Order changed by another user” | Reload the latest order, review the new state, and repeat only the action still needed. |
| A document, email, print job, or upload fails | Check the saved result/status first. Give an admin the order number, action, and exact error; do not repeatedly generate documents or send requests to test connectivity. |

When asking an AI for help, provide the screen name, order/part number, current status, intended outcome, and exact error. Specify whether you want it to inspect or take action. Uploads, generation/email, printing, signing, dispatch, shipping, run completion, and publishing change records or external systems; reviewing a page does not establish that one of those actions succeeded.

---

## Features

### Order Synchronization

- **Inflow API Polling**: The separate scheduler selects a 5-minute interval, or 30 minutes when webhooks are enabled and an active webhook is recorded at scheduler startup
- **Webhook Integration**: Real-time order updates via Inflow webhooks with fallback to polling
- **Smart Deduplication**: Creates or updates orders based on Inflow sales order ID
- **Webhook Maintenance**: Scheduled subscription reconciliation and health checks

`INFLOW_POLLING_SYNC_ENABLED` controls polling. The declared `INFLOW_POLLING_SYNC_INTERVAL_MINUTES` setting is currently unused by the scheduler; changing it does not change the interval.

### Preparation and Asset Tagging

- `/preparation` combines asset-tag requests, tagging completion, and batch picklist generation; the former `/tag-request` route redirects here
- Upload selected eligible tag requests to the Canopy Orders WebDAV integration, with optional Teams workflow notification
- Track who tagged an order and when; record tagging completion in bulk
- Apply configurable gates for asset tags, picker identity, separate QA reviewers, and partial-pick confirmation

### Partial Orders

- Generating a partial picklist creates a local picked child leg (`-P`, then `-P2`, etc.) and retains the remaining items on the parent order
- The picked child owns its generated documents and proceeds through QA and fulfillment independently
- Order tables and detail views show the items assigned to each leg; the remainder parent is blocked from QA while it still has unpicked items
- Subsequent Inflow refreshes reconcile the remainder without reclaiming quantities already assigned to earlier picked legs
- The parent's tagging state is cleared after a split so later picks can begin a fresh preparation cycle

### Location Intelligence

- **Building Code Extraction**: Maps addresses to TAMU building codes (ACAD, ZACH, LAAH, etc.) using ArcGIS
- **Order Remarks Parsing**: Extracts alternative delivery locations from order notes
- **Pattern Discovery**: Configurable patterns for location extraction

### Dual Delivery Workflows

**Local Delivery Flow:**

```text
Picked → QA → Pre-Delivery → In Delivery → Delivered
```

**Shipping Flow:**

```text
Picked → QA → Shipping → Mark Shipped → Delivered (carrier handoff)
```

### QA Checklist System

- In-app quality assurance replacing Google Forms
- Asset tag verification, packaging checks, documentation validation
- Order-based routing to Delivery or Shipping, shown on the checklist
- Blocking gates requiring completion before advancement

### Delivery Run Management

- Group multiple orders into delivery runs
- Vehicle assignment (van, golf cart)
- Pickup runs without a vehicle checkout
- Vehicle checkout and check-in records track who holds each vehicle
- Runner tracking and accountability
- Real-time status updates via WebSocket
- Bulk order status transitions

### Shipping Workflow

- Work Area, Dock, and Shipped states; the queue provides a direct Mark Shipped action from Work Area or Dock
- The API supports carrier and tracking fields, but the current Shipment Queue does not collect them
- Marking an order Shipped to Carrier automatically sets its overall status to Delivered; this records carrier handoff, not confirmed arrival at the recipient

### PDF Generation

- **Picklists**: Order items, quantities, serial numbers, signature line
- **Order Details**: Professional documents matching Inflow format
- ReportLab-based generation with TAMU branding

### Workstation Printing

- Optional automatic queueing of the first generated picklist, plus manually queued reprints
- A separate Windows agent claims jobs, downloads PDFs, prints through SumatraPDF, and reports completion or failure
- Socket.IO wakes the agent when work arrives; HTTP polling provides a fallback
- The agent uses a dedicated bearer token and configured printer, independent of browser login

See [the print-agent setup guide](ops/print_agent/README.md) for workstation dependencies and configuration.

### Email Notifications

- Microsoft Graph API integration
- Order details PDF delivery to recipients
- HTML email bodies; the legacy plain-text argument is retained for compatibility but is not used by Graph
- PDF attachment support
- Sending is controlled by the database-backed `email_notifications_enabled` setting

### Teams Notifications

- SharePoint folder queue strategy
- Power Automate flow integration
- Delivery status notifications to recipients
- Non-blocking async processing

### Document Signing

- In-app PDF signature capture
- Stylus/touch input support
- Signed document storage

### Real-time Updates

- Socket.IO WebSocket integration
- Live delivery run tracking
- Automatic reconnection with polling fallback
- Connection status indicators

### Audit Logging

- Order status history and action audit records
- User or system attribution on recorded events
- Timestamp recording
- Searchable audit trails

### Session Management

- Microsoft Entra OIDC login when configured, with SAML 2.0 fallback
- Persistent session tracking
- Session listing and management
- Secure cookie handling
- Operator and admin email allowlists

### Admin Dashboard

- System status overview
- Service health indicators (SharePoint, Email, Teams, Inflow)
- Webhook management and testing
- Manual sync triggers
- Notification testing tools
- Flow + Database observability (admin-only): recent activity timeline, order audit inspector, curated schema diagram, and table-level stats
- Database-backed controls for notifications, document signing, preparation gates, print queue policy, and access allowlists

### Product Vetting

- Admin-only `/vetting-editor` for maintaining products across the vetting lifecycle
- Add, edit, and remove entries with validation of required names and product URLs
- Load and save the vetting data through configured remote endpoints using WebDAV credentials

### Computer and Dock Compatibility

- `/compatibility-editor` maintains computer and dock records and their compatibility matrix
- Collaborative database editing with revision checks, pending contributions, and admin review
- An admin explicitly selects **Save to WebDAV** to publish an approved snapshot as `compatibility_superapp.json`
- The scheduler retries previously authorized publications that failed; editing or approving data alone does not publish it

### Inventory Reorder

- Admin-only `/inventory-reorder` combines Inflow availability and reorder thresholds with BigCommerce status 9 order demand
- Searchable, sortable summaries identify reorder and critical stock conditions, with a downloadable report
- Manual refreshes expose progress and enforce a cooldown
- When enabled, the separate scheduler refreshes daily at 7:30 AM, noon, and 3 PM in `America/Chicago` by default; times and timezone are configurable
- Optional Teams notifications support the inventory workflow

### Product Checker

- Admin-only `/product-checker` compares the visible BigCommerce catalog with included Inflow products
- Manually started, read-only scans report SKU matches and gaps, field differences, commodity-code issues, missing custom information, and closeout stock conditions
- Saved reports support search, pagination, links to source product records, and full JSON/text downloads
- Opening the page reads saved results; it does not start a scan or modify either catalog

See [Product Checker](docs/product-checker.md) for comparison scope, exclusions, and report interpretation.

### SharePoint Storage

- Document storage for picklists, QA records, signed documents
- Microsoft Graph API integration
- Folder-based organization

---

## Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│    Frontend     │◄───────►│     Backend      │◄───────►│     MySQL       │
│  React + Vite   │  HTTP/  │  Flask + SocketIO│   SQL   │    Database     │
│   TypeScript    │   WS    │                  │         │                 │
└─────────────────┘         └──────────────────┘         └─────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ▼                          ▼                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Inflow API    │     │  Microsoft Graph │     │    ArcGIS       │
│   (Inventory)   │     │  (Email, Teams,  │     │   (Buildings)   │
│                 │     │   SharePoint)    │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Technology Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | React 18, TypeScript, Vite, TailwindCSS, Socket.IO Client |
| **Backend** | Flask, SQLAlchemy, Flask-SocketIO, APScheduler, MSAL, python3-saml |
| **Database** | MySQL 8.0+ |
| **External APIs** | Inflow Cloud API, Microsoft Graph, ArcGIS, BigCommerce, WebDAV, Power Automate |

### Runtime Processes

| Process | Responsibility |
|---------|----------------|
| `backend/app/main.py` | Flask API, authentication, Socket.IO, `/health`, and the built SPA when `frontend/dist` exists |
| Vite development server | Local frontend with `/auth`, `/api`, and `/socket.io` proxies to port 8000 |
| `backend/run_scheduler.py` | Separate polling, webhook maintenance, inventory refresh, and authorized compatibility-publication retry jobs |
| `ops/print_agent/agent.py` | Optional Windows workstation worker for physical printing |

PythonAnywhere uses [backend/wsgi.py](backend/wsgi.py) for the web app and a separate Always-on task for the scheduler. Session cleanup and audit archival also have a traffic-driven maintenance path. GitHub Actions invokes [scripts/deploy.sh](scripts/deploy.sh) over SSH for configured deployments; see [deployment setup](docs/setup/deployment.md).

---

## Quick Start

### Prerequisites

- Python 3.12 (the CI-tested version)
- Node.js 20.19+ on the 20.x line, or 22.12+; the Vite 7 dependency does not support Node 18
- MySQL 8.0+ with a development database and user already created

### Backend Setup

Start from the repository root. In PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

In Bash on Linux/macOS:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` before starting the app:

- Set `DATABASE_URL` to the development database and replace `SECRET_KEY` with a private random value.
- For local development without SSO, set `FLASK_ENV=development` and `DEV_AUTH_BYPASS=true`. This enables a development identity with admin access; keep it disabled in deployed environments. Otherwise configure OIDC and the appropriate access allowlists.
- The example enables several integrations. For a local setup without external services, set `SCHEDULER_ENABLED=false`, `INFLOW_POLLING_SYNC_ENABLED=false`, `INFLOW_WEBHOOK_ENABLED=false`, `INFLOW_WEBHOOK_AUTO_REGISTER=false`, and `SHAREPOINT_ENABLED=false`. Leave external credentials unset until needed.

With the virtual environment active, run from `backend/`:

```sh
alembic upgrade head
python -m app.main
```

Keep that terminal open for the backend. This starts the development web server; it does not start the scheduler.

### Frontend Setup

In a second terminal, start from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

No frontend environment file is needed for the default local proxy configuration. See [frontend/.env.example](frontend/.env.example) for the optional `VITE_API_URL` override.

### Optional Scheduler

For an environment intended to sync with external services, configure its Inflow credentials and webhook settings, set `SCHEDULER_ENABLED=true`, and enable the required jobs in `backend/.env`. Run one scheduler process in a separate terminal with the backend virtual environment active:

```sh
# Run from backend/

python run_scheduler.py
```

Polling starts immediately when enabled. `INFLOW_WEBHOOK_AUTO_REGISTER=true` also enables webhook reconciliation at startup. Inventory refreshes default to enabled; use `INVENTORY_REORDER_SCHEDULED_REFRESH_ENABLED=false` if that integration is not configured. Stop the local scheduler with Ctrl+C when finished.

### Access Points

- **Frontend**: [http://localhost:5173](http://localhost:5173)
- **Backend API**: [http://localhost:8000/api](http://localhost:8000/api)
- **Health check**: [http://localhost:8000/health](http://localhost:8000/health)
- **Admin Panel**: [http://localhost:5173/admin](http://localhost:5173/admin)

---

## Configuration

Use [backend/.env.example](backend/.env.example) as a starting point, not an exhaustive list. [backend/app/config.py](backend/app/config.py) defines the environment-backed settings, defaults, and aliases; run backend commands from `backend/` so its `.env` is loaded. Additional workflow controls live in database-backed System Settings.

| Section | Variables |
|---------|-----------|
| **Database** | `DATABASE_URL` |
| **Inflow** | `INFLOW_API_URL`, `INFLOW_API_KEY`, `INFLOW_COMPANY_ID`, `INFLOW_POLLING_SYNC_ENABLED`, `INFLOW_WEBHOOK_*` |
| **Scheduler** | `SCHEDULER_ENABLED`; polling interval behavior is described under Order Synchronization |
| **User login** | `OIDC_TENANT_ID`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`; `SAML_*` for fallback |
| **Graph services** | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |
| **Sessions and access** | `SECRET_KEY`, `SESSION_COOKIE_NAME`, `SESSION_MAX_AGE_HOURS`, `ADMIN_EMAILS`, `ALLOWED_USER_EMAILS` |
| **Local auth** | `FLASK_ENV`, `DEV_AUTH_BYPASS`, `DEV_AUTH_EMAIL`, `DEV_AUTH_DISPLAY_NAME` |
| **Browser access** | `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` |
| **Storage** | `STORAGE_ROOT`, `TECHUB_DNS_LOCAL_STORAGE`, `SHAREPOINT_ENABLED`, `SHAREPOINT_SITE_URL`, `SHAREPOINT_FOLDER_PATH` |
| **Email / Teams** | `SMTP_FROM_ADDRESS` (Graph sender), `EMAIL_FROM_NAME`, `TEAMS_NOTIFICATION_QUEUE_FOLDER` |
| **Printing** | `PICKLIST_PRINT_AGENT_TOKEN` on the backend; matching `AGENT_TOKEN` and workstation options in `ops/print_agent/.env` |
| **Preparation** | `CANOPYORDERS_*` for the tag-request upload and Teams workflow integration |
| **Editors** | `VETTING_EDITOR_DOWNLOAD_URL`, `VETTING_EDITOR_UPLOAD_URL`, `WEBDAV_USERNAME`, `WEBDAV_PASSWORD`, `COMPATIBILITY_EDITOR_WEBDAV_FOLDER_URL` |
| **Inventory / product checks** | `INVENTORY_REORDER_BIGCOMMERCE_*`, Inflow credentials; `INVENTORY_REORDER_LOCATION_ID` and `INVENTORY_REORDER_SCHEDULED_REFRESH_*` for reorder reports |

OIDC credentials fall back to the corresponding `AZURE_*` values when the explicit `OIDC_*` settings are absent. Configure the login app's redirect URI and user access as well as credentials; see [authentication setup](docs/setup/authentication.md).

Email uses Microsoft Graph, not SMTP transport. Normal email sending is controlled by `email_notifications_enabled` in System Settings, not the legacy `SMTP_ENABLED` field. System Settings also controls recipient Teams notifications, signing, automatic picklist printing, and preparation gates; environment settings alone do not describe all feature behavior.

---

## Development and Testing

Run frontend commands from `frontend/` after `npm ci`:

```sh
npm run lint
npx tsc --noEmit
npm run build
```

`npm test` runs Vitest through a Windows-specific package script. On Linux/macOS, invoke the same installed runner directly from `frontend/`:

```sh
node node_modules/vitest/vitest.mjs run
```

Append a test file path to target a suite. Frontend tests use jsdom and Testing Library.

Run backend tests from `backend/` with the virtual environment active and a test/development database configuration. Some tests require database or integration setup; inspect the relevant suite before running it. Pytest is not included in the runtime requirements and must be installed separately for the suite command:

```sh
python -m pip install pytest
python -m pytest tests/ -q
```

Direct checks used by CI include:

```sh
python tests/test_error_handling.py
python tests/test_location_resolver.py
```

`npm run build` creates `frontend/dist`, which Flask serves when present. Do not edit generated output. See [AGENTS.md](AGENTS.md) for repository conventions and checks relevant to specific changes.

---

## Security

### Authentication & Authorization

- **Microsoft Entra login**: OIDC is preferred when configured; SAML is the fallback
- **API access**: Session middleware and route decorators protect operator/admin APIs. Auth callbacks, health checks, Inflow webhooks, and print-agent routes have separate public or service-auth handling
- **CSRF protection**: Origin/Referer verification is applied to logout and session-revocation endpoints; it is not a blanket guarantee for every state-changing API
- **Session management**: UUID-based sessions with expiry, revocation, and rolling last-seen

### Data Protection

- **Webhook signatures**: HMAC-SHA256 with timing-safe comparison when a configured or stored webhook secret is available; configure `INFLOW_WEBHOOK_SECRET` for signed webhook verification
- **Token security**: Bearer tokens compared with timing-safe functions
- **File path validation**: Picklist/document serving validated against storage root
- **Session key**: A missing `SECRET_KEY` generates an ephemeral key with a warning; set a stable private key for deployments

### Operational Security

- **Audit logging**: Workflow services record status changes and action details with actor and timestamp information
- **Error handling**: Central middleware serializes typed API failures and sanitizes unhandled errors
- **Webhook logging**: The webhook handler summarizes received payloads with event type and order number; downstream processing can log operational details such as addresses, so logs should be treated as sensitive

## Documentation

Start here: [docs/guide/index.md](docs/guide/index.md)

| Section | Purpose |
|---------|---------|
| [docs/guide/workflows.md](docs/guide/workflows.md) | Order lifecycle workflows |
| [docs/guide/operations.md](docs/guide/operations.md) | Day-to-day operations |
| [docs/reference/architecture.md](docs/reference/architecture.md) | System architecture |
| [docs/reference/api.md](docs/reference/api.md) | API reference |
| [docs/reference/database.md](docs/reference/database.md) | Database schema |
| [docs/reference/configuration.md](docs/reference/configuration.md) | Configuration reference |
| [docs/reference/troubleshooting.md](docs/reference/troubleshooting.md) | Troubleshooting |
| [docs/setup/deployment.md](docs/setup/deployment.md) | Deployment and auto-deploy setup |
| [docs/setup/authentication.md](docs/setup/authentication.md) | Microsoft Entra OIDC, SAML fallback, and Graph service configuration |
| [docs/setup/teams-notifications.md](docs/setup/teams-notifications.md) | Power Automate Teams notifications |
| [docs/product-checker.md](docs/product-checker.md) | Product comparison rules, reports, and scan lifecycle |
| [ops/print_agent/README.md](ops/print_agent/README.md) | Windows print-agent installation and configuration |
| [codemap.md](codemap.md) | Repository map and subsystem entry points |
| [docs/app-flow.md](docs/app-flow.md) | Product requirements document |
| [docs/plans/tag-request-integration.md](docs/plans/tag-request-integration.md) | Integration plan |

---

## Scripts

| Script | Purpose |
|--------|---------|
| `backend/scripts/database_manager.py` | Database and order management (interactive + CLI) |
| `backend/scripts/manage_inflow_webhook.py` | Inflow webhook subscription management |
| `backend/run_scheduler.py` | Separate background scheduler process |
| `backend/run_sync_once.py` | One-shot Inflow sync; may contact external services |
| `ops/print_agent/agent.py` | Windows picklist print worker |
| `scripts/deploy.sh` | PythonAnywhere deployment script invoked through GitHub Actions/SSH or manually |

Read operational scripts before running them. Sync, webhook management, notifications, printing, and deployment can affect external systems; they are not part of the basic local setup.

---

## License

Internal use only - Texas A&M University TechHub
