# TAMU Entra ID Authentication Setup Guide

This guide details how to configure the Azure applications required for the TechHub Delivery app:

1. **OIDC application**: Primary user login flow with Microsoft Entra account selection
2. **Legacy SAML application**: Fallback login path for deployments that still use SAML
3. **Service Principal**: Backend operations via Microsoft Graph API

## Prerequisites

- Azure Portal access with your TAMU credentials
- Permission to create Enterprise Applications (or request from IT)
- Admin consent for API permissions (or request from TAMU IT Security)

## 1. OIDC Application (User Authentication)

This application powers the `/login` button and should show Microsoft's account chooser by sending `prompt=select_account`.

### 1.1 Create App Registration

1. Log in to the [Azure Portal](https://portal.azure.com) with your TAMU credentials
2. Navigate to Microsoft Entra ID -> App registrations -> New registration
3. Name: `TechHub Delivery - User Login` (or similar)
4. Supported account types: Accounts in this organizational directory only (Single tenant)
5. Add a Web redirect URI for the backend callback:
   - Production: `https://techhub.pythonanywhere.com/auth/oidc/callback`
   - Local dev: `http://localhost:5173/auth/oidc/callback`
6. Click Register

### 1.2 Configure Credentials

1. Copy the Application (client) ID, Directory (tenant) ID, and a client secret into the backend environment.
2. The backend will use the Entra authorization endpoint with `prompt=select_account`.
3. Keep this app separate from the Graph/SharePoint service principal so login and backend storage permissions do not collide.

## 2. Legacy SAML Application (Fallback)

This path remains available at `/auth/saml/login` for existing deployments, but it is not the primary login button flow anymore.

### 2.1 Create Enterprise Application

1. Log in to the [Azure Portal](https://portal.azure.com) with your TAMU credentials
2. Navigate to Microsoft Entra ID -> Enterprise applications -> New application
3. Choose Create your own application
4. Name: `TechHub Delivery - User Auth` (or similar)
5. Select Integrate any other application you do not find in the gallery (Non-gallery)
6. Click Create

### 2.2 Configure Single Sign-On (SAML)

1. In the app overview, go to Single sign-on -> SAML
2. Configure Basic SAML Configuration:

| Field | Value |
|-------|-------|
| Identifier (Entity ID) | `https://techhub.pythonanywhere.com` |
| Reply URL (ACS URL) | `https://techhub.pythonanywhere.com/auth/saml/callback` |
| Sign on URL | `https://techhub.pythonanywhere.com/auth/saml/login` |
| Logout URL | `https://techhub.pythonanywhere.com/auth/logout` |

3. Configure Attributes and Claims:

| Claim Name | Source Attribute |
|------------|------------------|
| `display_name` | `user.displayname` |
| `email` | `user.userprincipalname` |
| `oid` | `user.objectid` |
| `department` | `user.department` (optional) |
| `employee_id` | `user.employeeid` (optional) |

### 2.3 Download Certificate and URLs

1. In the SAML Certificates section, download Certificate (Base64)
   - Save as `saml_idp_cert.crt` in `backend/certs/` (create folder if needed)
   - Note: This file is gitignored. Upload manually to production.

2. In the Set up [App Name] section, copy:
   - Login URL -> Set as `SAML_IDP_SSO_URL` in `.env`
   - Microsoft Entra Identifier -> Set as `SAML_IDP_ENTITY_ID` in `.env`

## 3. Service Principal (Backend Graph API)

This daemon application allows the backend to perform actions without a signed-in user.

### 3.1 Create App Registration

1. Navigate to Microsoft Entra ID -> App registrations -> New registration
2. Name: `TechHub Delivery - Backend Service`
3. Supported account types: Accounts in this organizational directory only (Single tenant)
4. Redirect URI: Leave blank (not needed for Service Principal)
5. Click Register

### 3.2 Get Credentials

1. **Client ID**: Copy Application (client) ID from Overview -> Set as `AZURE_CLIENT_ID`
2. **Tenant ID**: Copy Directory (tenant) ID -> Set as `AZURE_TENANT_ID`
3. **Client Secret**:
   - Go to Certificates and secrets -> New client secret
   - Description: "Backend API Access"
   - Expires: 24 months (recommended)
   - Copy the Value immediately (you will not see it again) -> Set as `AZURE_CLIENT_SECRET`

### 3.3 API Permissions

Go to API permissions -> Add a permission -> Microsoft Graph -> Application permissions.

Add the following permissions:

| Permission | Purpose |
|------------|---------|
| `Mail.Send` | Send emails as any user |
| `Sites.ReadWrite.All` | Upload files to SharePoint |
| `User.Read.All` | Look up user IDs (optional) |

**Admin Consent**:
- Click Grant admin consent for Texas A&M University
- If not an admin, request from TAMU IT Security (`cloudsecurity@tamu.edu`)
- Provide the App ID and business justification

## 4. Environment Configuration

Update your `.env` file with the gathered values:

```env
# ==============================================
# TAMU Entra ID Authentication
# ==============================================

# OIDC Login (Primary) / Legacy SAML Fallback
SAML_ENABLED=true
SAML_IDP_ENTITY_ID=<Microsoft Entra Identifier>
SAML_IDP_SSO_URL=<Login URL>
SAML_IDP_CERT_PATH=certs/saml_idp_cert.crt
SAML_SP_ENTITY_ID=https://techhub.pythonanywhere.com
SAML_ACS_URL=https://techhub.pythonanywhere.com/auth/saml/callback

# OIDC app registration
OIDC_TENANT_ID=<Directory (tenant) ID>
OIDC_CLIENT_ID=<Application (client) ID>
OIDC_CLIENT_SECRET=<Client Secret Value>

# Graph / SharePoint service principal
AZURE_TENANT_ID=<Directory (tenant) ID>
AZURE_CLIENT_ID=<Application (client) ID>
AZURE_CLIENT_SECRET=<Client Secret Value>
```

## 5. Certificate Management

### Upload to Production

The SAML certificate must be manually uploaded to production:

1. In PythonAnywhere Files tab, navigate to `~/techhub-dns/backend`
2. Create folder `certs` if it does not exist
3. Upload `saml_idp_cert.crt`
4. Verify path in `.env`: `SAML_IDP_CERT_PATH=certs/saml_idp_cert.crt`

### Certificate Renewal

SAML certificates expire periodically. To renew:

1. Download new certificate from Azure Portal
2. Replace `saml_idp_cert.crt` on production server
3. No code changes required

## 6. Verification

### Test Login

1. Start the application
2. Navigate to `/login`
3. Click "Sign In"
4. Should open Microsoft Entra and show the account chooser if multiple Entra sessions exist
5. After authentication, should redirect back to app

### Test Local Development Bypass

For local-only testing, you can bypass TAMU SSO with an explicit development flag:

```env
FLASK_ENV=development
DEV_AUTH_BYPASS=true
DEV_AUTH_EMAIL=dev.user@example.com
DEV_AUTH_DISPLAY_NAME=Local Dev User
DEV_AUTH_DEPARTMENT=Development
```

With this enabled:

1. `/auth/me` returns a synthetic local user/session.
2. `/login` stays inside the app instead of redirecting to Microsoft Entra.
3. `require_auth` and `require_admin` continue to work against the synthetic identity.

This bypass is only honored in development mode and should remain disabled in production.

### Test Graph API

1. Go to Admin panel (`/admin`)
2. Check Service Principal status in System Status
3. Use test buttons for SharePoint, Email, or Teams

## Troubleshooting

| Issue | Solution |
|-------|----------|
| SAML redirect loop | Check ACS URL matches exactly |
| Certificate error | Verify certificate path and file exists |
| Graph API 401 | Check client secret not expired |
| Graph API 403 | Admin consent not granted for permissions |
| "App not found in tenant" | Verify OIDC_TENANT_ID is correct |

## Security Notes

- Store client secrets securely (never commit to git)
- Use environment variables for all credentials
- Rotate client secrets before expiration
- Limit API permissions to minimum required
- Use service accounts for production flows
