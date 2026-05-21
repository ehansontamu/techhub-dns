# backend/app/api/routes/

## Responsibility
- Hosts the Flask blueprints that expose every HTTP API surface the Delivery app serves (analytics, audit, auth/session handling, order management, delivery runs, Inflow syncing/webhooks, observability, system health/configuration, SharePoint tooling, and vehicle checkouts).
- Each module wires request input validation, business services, and schema-driven JSON responses so the SPA and supporting admin tools can rely on a consistent request → service → schema pipeline.

## Design Patterns
- **Blueprint-per-domain.** Every file defines a `Blueprint` (or two, like vehicle status) that is mounted in `app/main.py`; shared helpers (`get_db`, `require_auth`, `require_admin`, schema factories) keep each module thin.
- **Service + schema pairs.** Routes import `app.services` helpers (e.g., `OrderService`, `DeliveryRunService`, `InflowService`, `PrintJobService`, `saml_auth_service`) and the matching `app.schemas` models for request validation (`CreateDeliveryRunRequest`, `OrderUpdate`, `WebhookRegisterRequest`, etc.) and response framing (`OrderResponse`, `DeliveryRunResponse`, analytics response DTOs, etc.).
- **Auth/protection layers.** `app.api.auth_middleware` decorators guard endpoints: `@require_auth` enforces session-based access for UI actions, `@require_admin` gates admin/observability routes, and the auth blueprint itself manages SAML login/callback, session cookies, and admin checks (`is_current_user_admin`).
- **Streaming/Realtime hooks.** Order/delivery/vehicle modules spawn background threads to call `_broadcast_*_sync` helpers that emit Socket.IO events via `app.main.socketio`, keeping dashboards live while HTTP requests stay responsive.

### Module domains
- `analytics.py`: read-only dashboard metrics (status counts, delivery performance, time trends, workflow trends, fulfilled totals) via `AnalyticsService` and dedicated response schemas.
- `audit.py`: exposes `ORDER/<uuid>` audit logs by querying `AuditLog` rows and serializing through `AuditLogResponse`.
- `auth.py`: handles Microsoft Entra login, OIDC callback, SAML fallback, `/me`, logout, session listing/revocation, and uses `saml_auth_service` plus cookie management/settings from `app.config.settings`.
- `orders.py`: the largest surface—fetch/list/resolve orders, transitions (single/bulk), tagging/picklist/QA/signing flows, shipping workflow updates, PDF/email downloads, SharePoint picklist retrieval, Teams notifications, asset-tag candidates, and `canopyorders` uploads; it ties `OrderService`, `InflowService`, `teams_recipient_service`, `pdf_service`, `email_service`, `sharepoint_service`, and schema classes (e.g., `OrderResponse`, `QASubmission`, `SignatureData`).
- `delivery_runs.py`: create/finish runs, recall/reorder orders, list runs, read available vehicles, and broadcast updates; protected by `require_auth` where state changes occur and uses `DeliveryRunService` with Pydantic request/response schemas.
- `inflow.py`: manual sync endpoints plus signature-verified webhooks, webhook registration/listing/recovery, and uses `InflowWebhook` models, `InflowService`, `OrderService`, and threads to broadcast order updates via `_broadcast_orders_sync`.
- `observability.py`: admin-only diagnostics (table stats, schema summary, system audit feed, runtime summary) by querying `Order`, `AuditLog`, `Session`, and audit models, with helpers to paginate and sanitize sensitive data.
- `system.py`: sprawling admin surface for system settings, admin allowlists, print job queueing/agent APIs, vetting/compatibility WebDAV editors, health tests (email, Teams, Inflow, SharePoint), sync triggers, Canopy order uploads, plus status endpoints that check SAML/Graph/SharePoint/Inflow; uses numerous services (`SystemSettingService`, `AuditService`, `PrintJobService`, `CanopyOrdersUploaderService`, `graph_service`, etc.).
- `sharepoint.py`: admin SharePoint tooling (status, authenticate, test upload) that relies on `SharePointService` and the MSAL-backed configuration in `app.config.settings`.
- `vehicle_checkouts.py`: check-in/out APIs and status listings protected by `require_auth`, plus a `/api/vehicles/status` route that reports current vehicle availability from `VehicleCheckoutService`.

## Flow
- **Request → service → schema.** Each route reads `request` data, constructs a Pydantic schema class (e.g., `CreateDeliveryRunRequest`, `OrderStatusUpdate`, `WebhookRegisterRequest`) to validate payloads/parameters, opens a `with get_db()` or `get_db_session()`, instantiates the relevant service, calls a business method, and then marshals the returned domain/state into a response schema before `jsonify` (often via `model_dump(mode="json")`). Missing or invalid IDs raise `abort` or `DNSApiError` subclasses so middleware can serialize errors.
- **Stateful side effects.** Updates that affect dashboards (order status, delivery runs, vehicle checkouts) kick off background threads that re-query state and emit Socket.IO messages. Teams/email notifications are queued after `OrderService` transitions reach `IN_DELIVERY`. Picklist signing/QA/picklist generation tie back into `PrintJobService`, `pdf_service`, or `email_service` before returning success metadata.
- **Specialized flows.** Webhook routes in `inflow.py` verify incoming HMAC signatures using stored `InflowWebhook` secrets, fetch full order payloads from Inflow, and delegate to `OrderService.create_order_from_inflow`, tracking failure counts. The auth blueprint prepares Microsoft Entra OIDC and python3-saml requests, handles Entra redirects, and persists sessions with secure cookies. System print-agent endpoints require a bearer token (`_require_print_agent`) before touching `PrintJobService` and emitting websocket notices.

## Integration
- **Database models.** Every route layers on SQLAlchemy models (`Order`, `DeliveryRun`, `InflowWebhook`, `AuditLog`, `SystemSetting`, `Session`, etc.) via the service layer to avoid exposing raw ORM objects to the HTTP surface.
- **Middleware.** Auth decorators and context (`g.user_id`, `g.user_data`) live in `app.api.auth_middleware`; observability and system settings reuse shared helpers like `get_rate_limit_snapshot` and the `require_admin` guard.
- **External systems.**
  - **Inflow API:** sync endpoints, webhooks, order fulfillment, and asset-tag logic all call `InflowService` (via `sync_recent_started_orders_sync`, `get_order_by_number_sync`, `fulfill_sales_order_sync`).
  - **TAMU SAML / Entra ID:** `auth.py` and `_get_saml_status` use Microsoft Entra OIDC plus `python3-saml` to authenticate and persist sessions.
  - **Microsoft Graph / Teams / SharePoint:** Teams notifications (`teams_recipient_service`), SharePoint uploads/downloads (`SharePointService`), and Graph-backed status checks (`graph_service`, `sharepoint_service._get_access_token`). Vetting/compatibility editor endpoints talk to remote WebDAV endpoints secured with Azure identities.
  - **Email & PDF:** `pdf_service`, `email_service`, and `pdf_service.generate_order_details_pdf` support downloads and outgoing emails, often triggered by order or system routes.
  - **Print tooling & Canopy upload:** `PrintJobService`, `emit_orders_update`, and `CanopyOrdersUploaderService` surface job status, require-agent flows, and upload notifications.
  - **Realtime / Socket.IO:** `_broadcast_orders_sync`, `_broadcast_active_runs_sync`, and `broadcast_vehicle_status_update_sync` emit payloads to `socketio` rooms so dashboards stay current.
  - **Telemetry/Observability:** Admin routes query DB stats, audit feeds, and runtime DB pool settings to give operators visibility.

Finally, the system health endpoints in `system.py` and `observability.py` tie these integrations together by reporting statuses for SAML, Graph, SharePoint, Inflow sync, and webhooks, making this folder the authoritative map of backend HTTP behavior.
