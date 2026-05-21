# Operations

This guide covers day-to-day system operations, integrations, and monitoring.

## Email Service

### Overview

Email sending via Microsoft Graph API using Service Principal authentication.

**Service**: `backend/app/services/email_service.py`

### Configuration

```env
SMTP_ENABLED=true                    # Enable/disable email sending
SMTP_FROM_ADDRESS=techhub@tamu.edu   # Sender address
EMAIL_FROM_NAME=TechHub              # Display name
```

**Required Graph Permissions**: `Mail.Send`

### Email Methods

#### General Email
```python
email_service.send_email(
    to_address="recipient@tamu.edu",
    subject="Subject Line",
    body_html="<p>HTML content</p>",
    body_text="Plain text fallback",
    attachment_name="document.pdf",
    attachment_content=pdf_bytes
)
```

#### Order Details Email
```python
email_service.send_order_details_email(
    to_address="recipient@tamu.edu",
    order_number="TH4013",
    customer_name="John Smith",
    pdf_content=pdf_bytes
)
```

## Teams Notification Service

### Overview

Teams notifications via SharePoint folder queue and Power Automate flow.

**Service**: `backend/app/services/teams_recipient_service.py`

### Architecture

```
Backend -> SharePoint Queue Folder -> Power Automate Flow -> Teams Message
```

### Queue Strategy

1. Backend creates JSON notification file
2. Uploads to SharePoint queue folder
3. Power Automate monitors folder for new files
4. Flow reads JSON and sends Teams message
5. Flow deletes processed file

### Notification Payload

```json
{
  "id": "notif_TH4013_1737500000",
  "type": "delivery_notification",
  "recipientEmail": "recipient@tamu.edu",
  "recipientName": "John Smith",
  "orderNumber": "TH4013",
  "deliveryRunner": "Jane Doe",
  "estimatedTime": "Shortly",
  "createdAt": "2026-01-21T15:00:00"
}
```

### Configuration

```env
TEAMS_RECIPIENT_NOTIFICATIONS_ENABLED=true
TEAMS_NOTIFICATION_QUEUE_FOLDER=notifications-queue
```

See [Teams notifications setup](../setup/teams-notifications.md) for Power Automate configuration.

## Real-time Updates

### Overview

Socket.IO WebSocket integration for live delivery tracking.

**Backend**: Flask-SocketIO in `backend/app/main.py`
**Frontend**: `frontend/src/hooks/useDeliveryRuns.ts`

### Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `connect` | Client->Server | Client connects |
| `active_runs` | Server->Client | Broadcast active delivery runs |
| `disconnect` | Client->Server | Client disconnects |

### Message Format

```json
{
  "type": "active_runs",
  "data": [
    {
      "id": "uuid",
      "name": "Morning Run 1",
      "runner": "Jane Doe",
      "vehicle": "van",
      "status": "Active",
      "orders": [...]
    }
  ]
}
```

### Fallback

If WebSocket connection fails, frontend falls back to HTTP polling.

## Audit Logging

### Overview

Complete audit trail of all status changes and actions.

**Service**: `backend/app/services/audit_service.py`
**Model**: `backend/app/models/audit_log.py`

### Order Audit Log

```python
class AuditLog:
    id: String(36)           # UUID
    order_id: String(36)     # Foreign key to Order
    changed_by: String       # User who made change
    from_status: String      # Previous status
    to_status: String        # New status
    reason: Text             # Optional reason
    timestamp: DateTime      # When change occurred
    metadata: JSON           # Additional context
```

### System Audit Log

```python
class SystemAuditLog:
    id: String(36)
    entity_type: String      # 'order', 'delivery_run', 'webhook'
    entity_id: String
    action: String           # 'create', 'update', 'delete'
    description: Text
    user_id: String
    user_role: String
    old_value: JSON
    new_value: JSON
    metadata: JSON
    ip_address: String
    user_agent: Text
    timestamp: DateTime
```

### Viewing Audit Logs

**Endpoint**: `GET /api/orders/{order_id}/audit`

Audit logs displayed on order detail page with:
- Timestamp
- User attribution
- Status change details
- Reason (if provided)

## Authentication and Sessions

### SAML Authentication

**Service**: `backend/app/services/saml_auth_service.py`

Texas A&M SSO via SAML 2.0:

1. User clicks "Sign In"
2. Redirect to TAMU IdP
3. User authenticates
4. IdP POSTs assertion to `/auth/saml/callback`
5. Backend creates session
6. User redirected to application

**Configuration**:
```env
SAML_ENABLED=true
SAML_IDP_ENTITY_ID=<Microsoft Entra Identifier>
SAML_IDP_SSO_URL=<Login URL>
SAML_IDP_CERT_PATH=certs/saml_idp_cert.crt
SAML_SP_ENTITY_ID=https://your-domain
SAML_ACS_URL=https://your-domain/auth/saml/callback
```

For local development, you can bypass SAML entirely:

```env
FLASK_ENV=development
DEV_AUTH_BYPASS=true
DEV_AUTH_EMAIL=dev.user@example.com
DEV_AUTH_DISPLAY_NAME=Local Dev User
DEV_AUTH_DEPARTMENT=Development
```

This path is intended only for local testing. It should stay disabled in production.

### Session Management

**Model**: `backend/app/models/session.py`

```python
class Session:
    id: String(36)           # Session ID (cookie value)
    user_id: String(36)      # Foreign key to User
    created_at: DateTime
    expires_at: DateTime
    ip_address: String
    user_agent: Text
```

**Configuration**:
```env
SESSION_COOKIE_NAME=techhub_session
SESSION_MAX_AGE_HOURS=168
```

### User Model

```python
class User:
    id: String(36)           # UUID
    email: String            # UPN from SAML
    display_name: String     # From SAML assertion
    department: String       # Optional
    employee_id: String      # Optional
    created_at: DateTime
    last_login_at: DateTime
```

## SharePoint Storage

### Overview

Document storage via Microsoft Graph API.

**Service**: `backend/app/services/sharepoint_service.py`

### Configuration

```env
SHAREPOINT_ENABLED=true
SHAREPOINT_SITE_URL=https://tamucs.sharepoint.com/teams/Team-TechHub
SHAREPOINT_FOLDER_PATH=General/delivery-storage
```

**Required Graph Permissions**: `Sites.ReadWrite.All`

### Storage Structure

```
SharePoint Site/
└── Documents/
    └── General/
        └── delivery-storage/
            ├── picklists/
            │   └── TH4013.pdf
            ├── qa/
            │   └── TH4013_qa.json
            ├── signed/
            │   └── TH4013_signed.pdf
            └── notifications-queue/
                └── notification_TH4013_*.json
```

### Methods

```python
# Upload file
sharepoint_service.upload_file(content, subfolder, filename)

# Upload JSON
sharepoint_service.upload_json(data, subfolder, filename)

# Download file
content = sharepoint_service.download_file(subfolder, filename)

# Check existence
exists = sharepoint_service.file_exists(subfolder, filename)
```

## Admin Dashboard

### Overview

System administration and monitoring interface.

**Page**: `frontend/src/pages/Admin.tsx`

### System Status Cards

| Service | Status Check |
|---------|--------------|
| Database | Connection test |
| Inflow API | API connectivity |
| SharePoint | Graph API + site access |
| Email | Graph API configuration |
| Teams | Queue folder access |

### Webhook Management

- View registered webhooks
- Register new webhooks
- Delete webhooks
- View webhook status and failure counts

### Testing Tools

| Tool | Purpose |
|------|---------|
| Manual Sync | Trigger Inflow sync |
| Test SharePoint | Upload test file |
| Test Email | Send test email |
| Test Teams | Queue test notification |

### System Settings

- Feature toggles
- Environment configuration display
- Sync status and history
