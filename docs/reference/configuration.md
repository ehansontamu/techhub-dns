# Configuration Reference

## Required Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | MySQL connection string |
| `INFLOW_API_URL` | Inflow Cloud API base URL |
| `INFLOW_API_KEY` | Inflow API key |
| `INFLOW_COMPANY_ID` | Inflow company ID |
| `SECRET_KEY` | Flask secret key (if omitted, an ephemeral random key is generated at startup; sessions will not persist across restarts) |

## Authentication

| Variable | Description |
|----------|-------------|
| `SAML_ENABLED` | Enable SAML authentication |
| `SAML_IDP_ENTITY_ID` | IdP entity ID |
| `SAML_IDP_SSO_URL` | IdP login URL |
| `SAML_IDP_CERT_PATH` | Path to IdP certificate |
| `SAML_SP_ENTITY_ID` | Service provider entity ID |
| `SAML_ACS_URL` | Assertion consumer service URL |
| `DEV_AUTH_BYPASS` | Development-only auth bypass that synthesizes a local user/session instead of using TAMU SSO |
| `DEV_AUTH_EMAIL` | Email for the synthetic local dev user |
| `DEV_AUTH_DISPLAY_NAME` | Display name for the synthetic local dev user |
| `DEV_AUTH_DEPARTMENT` | Optional department label for the synthetic local dev user |
| `OIDC_TENANT_ID` | Azure AD tenant ID used for interactive login |
| `OIDC_CLIENT_ID` | Azure app client ID used for interactive login |
| `OIDC_CLIENT_SECRET` | Azure app client secret used for interactive login |
| `AZURE_TENANT_ID` | Azure AD tenant ID used for Graph access, SharePoint, and email |
| `AZURE_CLIENT_ID` | Azure app client ID used for Graph access, SharePoint, and email |
| `AZURE_CLIENT_SECRET` | Azure app client secret used for Graph access, SharePoint, and email |

## Admin Authorization

| Variable | Description |
|----------|-------------|
| `ADMIN_EMAILS` | Admin email allowlist (recommended: comma-separated). Leave this unset to use the default behavior; do not set an explicit empty value (avoid `ADMIN_EMAILS=`). If empty/unset: admin endpoints allow any authenticated user ONLY in `development`; in other environments access is denied (fail-closed). |

### Admin-Only Observability Endpoints

These endpoints are intended for the Admin UI (Flow + Database tabs) and are always protected by admin allowlisting.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/observability/schema-summary` | Curated, allowlisted schema model for ER-ish visualization. Column names are filtered to avoid secret/token fields. |
| `GET /api/observability/table-stats` | Allowlisted table stats (row counts + freshness timestamps where available). |
| `GET /api/observability/system-audit` | Paginated system audit feed (filters: entity_type/entity_id/action/since/cursor). Value payloads are excluded by default. |

## Features

| Variable | Description |
|----------|-------------|
| `SHAREPOINT_ENABLED` | Enable SharePoint storage |
| `SHAREPOINT_SITE_URL` | SharePoint site URL |
| `SHAREPOINT_FOLDER_PATH` | Base folder path |
| `SMTP_ENABLED` | Enable email sending |
| `SMTP_FROM_ADDRESS` | Sender email address |
| `TEAMS_RECIPIENT_NOTIFICATIONS_ENABLED` | Enable Teams notifications |
| `TEAMS_NOTIFICATION_QUEUE_FOLDER` | SharePoint queue folder |

## Sync Configuration

| Variable | Description |
|----------|-------------|
| `INFLOW_POLLING_SYNC_ENABLED` | Enable polling sync |
| `INFLOW_POLLING_SYNC_INTERVAL_MINUTES` | Sync interval |
| `INFLOW_WEBHOOK_ENABLED` | Enable webhook sync |
| `INFLOW_WEBHOOK_URL` | Webhook receiver URL |
| `INFLOW_WEBHOOK_EVENTS` | Events to subscribe |
| `INFLOW_WEBHOOK_AUTO_REGISTER` | Auto-register on startup |

## Deployment

| Variable | Description |
|----------|-------------|
| `FLASK_ENV` | Environment (development/production) |
| `FRONTEND_URL` | Browser-facing frontend origin for CORS and OIDC redirect URIs |
