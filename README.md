# TechHub Delivery Workflow

A comprehensive order fulfillment and delivery management system for Texas A&M University's TechHub. This application manages the complete lifecycle of hardware orders from inventory picking through delivery, signature capture, and final fulfillment.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Documentation](#documentation)

---

## Overview

The TechHub Delivery Workflow App streamlines order delivery operations by integrating with Inflow inventory management, Microsoft 365 services, and ArcGIS location services. It replaces fragmented scripts and manual processes with a unified, auditable system.

### Key Capabilities

| Category | Features |
|----------|----------|
| **Order Management** | Inflow sync (polling + webhooks), smart location extraction, order remarks parsing |
| **Dual Workflows** | Local delivery runs with vehicle tracking, shipping with carrier integration |
| **Quality Assurance** | In-app QA checklists, prep step gating, workflow routing |
| **Documents** | PDF generation (picklists, order details), in-app signature capture |
| **Notifications** | Email via Graph API, Teams via SharePoint queue + Power Automate |
| **Real-time** | WebSocket updates for live delivery tracking |
| **Audit & Security** | Complete audit trails, TAMU SSO (SAML), session management |

---

## Features

### Order Synchronization
- **Inflow API Polling**: Automatic sync of picked orders every 5-20 minutes (configurable)
- **Webhook Integration**: Real-time order updates via Inflow webhooks with fallback to polling
- **Smart Deduplication**: Creates or updates orders based on Inflow sales order ID

### Location Intelligence
- **Building Code Extraction**: Maps addresses to TAMU building codes (ACAD, ZACH, LAAH, etc.) using ArcGIS
- **Order Remarks Parsing**: Extracts alternative delivery locations from order notes
- **Pattern Discovery**: Configurable patterns for location extraction

### Dual Delivery Workflows

**Local Delivery Flow:**
```
Picked → QA → Pre-Delivery → In Delivery → Delivered
```

**Shipping Flow:**
```
Picked → QA → Pre-Delivery → Shipping (Work Area → Dock → Shipped) → Delivered
```

### QA Checklist System
- In-app quality assurance replacing Google Forms
- Asset tag verification, packaging checks, documentation validation
- Workflow method selection (Delivery vs Shipping)
- Blocking gates requiring completion before advancement

### Delivery Run Management
- Group multiple orders into delivery runs
- Vehicle assignment (van, golf cart)
- Runner tracking and accountability
- Real-time status updates via WebSocket
- Bulk order status transitions

### Shipping Workflow
- Three-stage process: Work Area → Dock → Shipped to Carrier
- Carrier name and tracking number capture
- Sequential stage enforcement
- Automatic status transitions

### PDF Generation
- **Picklists**: Order items, quantities, serial numbers, signature line
- **Order Details**: Professional documents matching Inflow format
- ReportLab-based generation with TAMU branding

### Email Notifications
- Microsoft Graph API integration
- Order details PDF delivery to recipients
- HTML + plain text email bodies
- PDF attachment support

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
- Complete status change history
- User attribution for all actions
- Timestamp recording
- Searchable audit trails

### Session Management
- TAMU SSO via SAML 2.0
- Persistent session tracking
- Session listing and management
- Secure cookie handling

### Admin Dashboard
- System status overview
- Service health indicators (SharePoint, Email, Teams, Inflow)
- Webhook management and testing
- Manual sync triggers
- Notification testing tools
- Flow + Database observability (admin-only): recent activity timeline, order audit inspector, curated schema diagram, and table-level stats

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
| **External APIs** | Inflow Cloud API, Microsoft Graph, ArcGIS |

---

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 18+
- MySQL 8.0+

### Backend Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows
# source .venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
cp .env.example .env
# Edit .env with your configuration

alembic upgrade head
python -m app.main
```

### Frontend
```bash
cd frontend
npm test          # Run tests (vitest)
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

### Access Points
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **Admin Panel**: http://localhost:5173/admin

---

## Configuration

See `.env.example` for all configuration options. Key sections:

| Section | Variables |
|---------|-----------|
| **Database** | `DATABASE_URL` |
| **Inflow** | `INFLOW_API_URL`, `INFLOW_API_KEY`, `INFLOW_COMPANY_ID` |
| **Authentication** | `SAML_*` (legacy fallback login), `OIDC_*` (user login), `AZURE_*` (service principal) |
| **SharePoint** | `SHAREPOINT_SITE_URL`, `SHAREPOINT_FOLDER_PATH` |
| **Features** | `TEAMS_RECIPIENT_NOTIFICATIONS_ENABLED`, `SMTP_ENABLED` |

---

## Security

### Authentication & Authorization
- **TAMU SSO (SAML)** for production authentication
- **Route-level auth**: All API endpoints require `@require_auth` or `@require_admin`
- **CSRF protection**: Origin/Referer verification on state-changing endpoints
- **Session management**: UUID-based sessions with expiry, revocation, and rolling last-seen

### Data Protection
- **Webhook signatures**: HMAC-SHA256 with timing-safe comparison (`hmac.compare_digest`)
- **Token security**: Bearer tokens compared with timing-safe functions
- **File path validation**: Picklist/document serving validated against storage root
- **No hardcoded secrets**: Missing SECRET_KEY generates ephemeral key with warning

### Operational Security
- **Audit logging**: All order mutations recorded with actor, timestamps, and change details
- **Error sanitization**: Internal errors logged server-side; generic messages sent to clients
- **Payload logging**: Webhooks log event type + order number only (no PII)

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
| [docs/setup/authentication.md](docs/setup/authentication.md) | Azure AD and SAML configuration |
| [docs/setup/teams-notifications.md](docs/setup/teams-notifications.md) | Power Automate Teams notifications |
| [docs/app-flow.md](docs/app-flow.md) | Product requirements document |
| [docs/plans/tag-request-integration.md](docs/plans/tag-request-integration.md) | Integration plan |

---

## Scripts

| Script | Purpose |
|--------|---------|
| `backend/scripts/database_manager.py` | Database and order management (interactive + CLI) |
| `backend/scripts/manage_inflow_webhook.py` | Inflow webhook subscription management |
| `scripts/deploy.sh` | GitHub webhook auto-deploy script |

---

## License

Internal use only - Texas A&M University TechHub
