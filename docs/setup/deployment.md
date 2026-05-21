# Deployment Setup

Complete guide for deploying the TechHub Delivery application to PythonAnywhere with Flask-SocketIO WebSocket support and GitHub Actions automation.

## Prerequisites

- PythonAnywhere account (Web Dev tier or higher for SSH access)
- MySQL database configured on PythonAnywhere
- Git access to the repository
- Node.js available on PythonAnywhere (for building frontend during deploy)
- Azure AD configuration complete (see [authentication setup](authentication.md))

## Local Preparation

### Frontend Build Flow

The frontend is built on PythonAnywhere during deployment, so a local build is not required.
If you want to validate locally, you can still run:

```powershell
cd frontend
npm install
npm run build
```

## PythonAnywhere Initial Setup

### Clone the Repository

Open a Bash console on PythonAnywhere:

```bash
cd ~
git clone https://github.com/kyler505/techhub-dns.git techhub-dns
cd techhub-dns/backend
```

### Create Virtual Environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn eventlet
```

### Install PythonAnywhere CLI

```bash
pip install --upgrade pythonanywhere
```

Generate API token:
1. Go to Account -> API Token
2. Click Create a new API token

## Configuration

### Environment Variables

```bash
cp .env.example .env
nano .env
```

Update these values:

```env
# Database
DATABASE_URL=mysql+pymysql://username:password@username.mysql.pythonanywhere-services.com/username$database

# Inflow
INFLOW_API_KEY=your_inflow_api_key
INFLOW_COMPANY_ID=your_company_id

# Authentication
# Primary login: OIDC app registration
# Legacy login fallback: SAML enterprise app
FRONTEND_URL=https://username.pythonanywhere.com

# OIDC login app registration
OIDC_TENANT_ID=<from Azure>
OIDC_CLIENT_ID=<from Azure>
OIDC_CLIENT_SECRET=<from Azure>

# Legacy SAML app registration
SAML_ENABLED=true
SAML_IDP_ENTITY_ID=<from Azure>
SAML_IDP_SSO_URL=<from Azure>
SAML_IDP_CERT_PATH=certs/saml_idp_cert.crt

# App Settings
FLASK_ENV=production
```

### Upload SAML Certificate

The certificate is not in git. Upload manually:

1. In PythonAnywhere Files tab, navigate to `~/techhub-dns/backend`
2. Create folder `certs` if it does not exist
3. Upload `saml_idp_cert.crt`

### OIDC App Registration for Login Button

Create a separate Microsoft Entra app registration for the interactive login flow:

1. Open the Entra ID toolkit and create a new **OpenID Connect** application.
2. Set the redirect URI to:
   - Production: `https://username.pythonanywhere.com/auth/oidc/callback`
   - Local dev: `http://localhost:5173/auth/oidc/callback`
3. Copy the app's tenant ID, client ID, and client secret into the `OIDC_*` variables above.
4. Keep the SAML app in place if you still want the legacy fallback path.

## Database Setup

### Create MySQL Database

1. Go to Databases tab in PythonAnywhere
2. Create a new MySQL database
3. Note the connection details:
   - Database name: `username$dbname`
   - Username: `username`
   - Password: (set during creation)
   - Host: `username.mysql.pythonanywhere-services.com`

### Initialize Database

```bash
cd ~/techhub-dns/backend
source .venv/bin/activate

# Create tables
python -c "from app.database import engine, Base; from app.models import *; Base.metadata.create_all(bind=engine)"

# Or use Alembic migrations
alembic upgrade head
```

## Deploy with ASGI

### Create the Website

Run this command (ASGI beta for WebSocket support):

```bash
pa website create \
  --domain username.pythonanywhere.com \
  --command '/home/username/techhub-dns/backend/.venv/bin/gunicorn --worker-class eventlet -w 1 --chdir /home/username/techhub-dns/backend --bind unix:${DOMAIN_SOCKET} app.main:app'
```

Replace `username` with your PythonAnywhere username.

### Create the Background Scheduler Task

For PythonAnywhere, keep the web app focused on requests and run the Inflow
polling scheduler as a single Always-on task.

Command:

```bash
cd /home/username/techhub-dns/backend && /home/username/techhub-dns/backend/.venv/bin/python run_scheduler.py
```

Recommended behavior:

- Keep exactly one Always-on task running this command.
- Keep the web app on a single worker unless you later add a shared Socket.IO broker.
- `SCHEDULER_ENABLED` now controls the standalone scheduler runner. The web app no
  longer starts APScheduler on incoming requests.

If you do not want polling fallback, set `SCHEDULER_ENABLED=false` and rely on
webhooks only.

### Alternative: Scheduled Task for Dev

If `dev` does not need a persistent Always-on task, use a PythonAnywhere hourly
scheduled task instead of `run_scheduler.py`.

Command:

```bash
cd /home/username/techhub-dns-dev/backend && /home/username/techhub-dns-dev/backend/.venv/bin/python run_sync_once.py
```

Use this for lower-priority environments where hourly catch-up sync is good
enough. Keep `prod` on the Always-on `run_scheduler.py` worker.

### Verify Deployment

Visit `https://username.pythonanywhere.com` - you should see the React frontend.
Then confirm the Always-on task is running and check its log output after the
first poll interval.

## Managing Your Site

### Common Commands

| Command | Description |
|---------|-------------|
| `pa website get` | List all ASGI websites |
| `pa website get --domain username.pythonanywhere.com` | Get site details |
| `pa website reload --domain username.pythonanywhere.com` | Reload after code changes |
| `pa website delete --domain username.pythonanywhere.com` | Delete the website |

Always-on task management is done from the PythonAnywhere Tasks tab.

### View Logs

```bash
# Access log
cat /var/log/username.pythonanywhere.com.access.log

# Error log
cat /var/log/username.pythonanywhere.com.error.log

# Server log
cat /var/log/username.pythonanywhere.com.server.log
```

## Updating the Application

### Standard Update Process

1. On your local machine:
```powershell
git add .
git commit -m "Update application"
git push
```

2. On PythonAnywhere:
```bash
cd ~/techhub-dns
bash scripts/deploy.sh
```

## Local Dev Testing

To test the OIDC chooser flow locally:

1. Set `FRONTEND_URL=http://localhost:5173`
2. Set `OIDC_TENANT_ID`, `OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET` for the OIDC app registration
3. Start the backend on port `8000`
4. Start the frontend on port `5173`
5. Click the login button and confirm the browser goes through `/auth/login` and returns to `/auth/oidc/callback`

The deploy script runs `npm ci` and `npm run build` in `frontend/`, then reloads the app.
If you changed scheduler code or env vars, restart the Always-on task after the deploy.

## Automated Deployment (GitHub Actions)

### Overview

On every push to `main`, GitHub Actions SSHes into PythonAnywhere and runs:

```bash
bash ~/techhub-dns/scripts/deploy.sh
```

Logs stream in the Actions job output. If the deploy fails, the workflow tails `~/techhub-dns/deploy.log` and uploads it as an artifact.

### Release Gate Checklist

The workflow now uses branch-specific gates before remote deployment:

1. All deploy branches (`dev` and `main`) must pass a fast backend gate:
   - `python3 -m compileall backend/app`
2. `main` branch (and PRs targeting `main`) must also pass full quality gates:
   - `cd backend && python3 tests/test_error_handling.py`
   - `cd backend && python3 tests/test_location_resolver.py`
   - `cd frontend && npm run lint`
   - `cd frontend && npm run build`

If any required gate for the target branch fails, deployment is blocked.

### Setup Steps

#### 1. Add GitHub repository secrets

In your GitHub repo: Settings -> Secrets and variables -> Actions -> New repository secret

| Secret name | Value |
|------------|-------|
| `PA_USERNAME` | Your PythonAnywhere username |
| `PA_SSH_PRIVATE_KEY` | Private SSH key that can SSH to PythonAnywhere |

Notes:
- Use an SSH key that is authorized for your PythonAnywhere account.
- Keep the private key unencrypted (no passphrase) for Actions, or configure an agent-compatible key flow.

#### 2. Ensure PythonAnywhere can accept SSH

1. In PythonAnywhere, add the SSH public key for the GitHub Actions keypair (Account -> SSH keys).
2. Confirm you can SSH from your local machine using that key.

#### 3. Verify the workflow

The workflow file is in this repo at:

`/.github/workflows/deploy-pythonanywhere.yml`

It triggers on pushes to `main` and via manual `workflow_dispatch`.

#### 4. Remove the old webhook (if configured)

If you previously configured a GitHub webhook pointing at `/api/system/deploy`, delete or disable it:

GitHub repo -> Settings -> Webhooks

### Deployment Process

When triggered, the deploy script (`scripts/deploy.sh`) performs:

1. `git fetch origin main`
2. `git reset --hard origin/main`
3. `npm ci` + `npm run build` in `frontend/`
4. Logs recent commits
5. Touches WSGI file to trigger reload

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Actions job fails with "Permission denied" | Verify `PA_SSH_PRIVATE_KEY` matches a public key in PythonAnywhere (Account -> SSH keys) and `PA_USERNAME` is correct |
| Actions job fails with "Could not resolve hostname" | Confirm PythonAnywhere SSH host is `ssh.pythonanywhere.com` |
| Actions job fails during frontend build | Check the job output and `~/techhub-dns/deploy.log` artifact (uploaded on failure) |
| Deploy script missing | Ensure `~/techhub-dns/scripts/deploy.sh` exists on the server |

### Manual Deploy

If needed, you can still deploy manually via SSH:

```bash
cd ~/techhub-dns
bash scripts/deploy.sh
```



## Architecture Notes

### How the Frontend is Served

The Flask backend serves the React frontend from `frontend/dist/`:

- API routes (`/api/*`): Handled by Flask blueprints
- Auth routes (`/auth/*`): SAML authentication
- Static files: React build assets
- All other routes: Serve React SPA with fallback to `index.html`
- WebSocket: Flask-SocketIO via eventlet

### Path Detection

The backend automatically detects the frontend dist path:
1. `/home/username/techhub-dns/frontend/dist` (PythonAnywhere)
2. Relative path from backend (local development)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Frontend not built" | Run `bash scripts/deploy.sh` to rebuild frontend |
| 404 for a few seconds | Normal on ASGI beta - just refresh |
| ModuleNotFoundError | Check virtualenv path is correct |
| WebSocket not connecting | Verify `cors_allowed_origins="*"` in SocketIO config |
| Database errors | Verify DATABASE_URL in .env |
| SAML login fails | Check certificate path and IdP URLs |
| Git push fails | Run `git config http.postBuffer 524288000` |

### Fixing Alembic revision overlap (multiple rows in `alembic_version`)

If deploy fails with an error like:

`Requested revision 0006_add_vehicle_checkouts overlaps with other requested revisions 0005_archive_system_audit_and_session_indexes`

it usually means the `alembic_version` table contains multiple rows (for example, both `0005` and `0006`). Alembic expects exactly one row.

1) Inspect the current rows (no secrets printed):

```bash
cd ~/techhub-dns/backend
source .venv/bin/activate

python - <<'PY'
from sqlalchemy import create_engine, text
from app.config import settings

engine = create_engine(settings.database_url)
with engine.connect() as conn:
    rows = conn.execute(text("select version_num from alembic_version")).fetchall()
print("alembic_version rows:", [r[0] for r in rows])
PY
```

2) Identify the latest revision to keep:

```bash
python -m alembic heads
```

3) Delete older rows, leaving only the latest revision. For example, if the latest is `0006_add_vehicle_checkouts`, delete everything else:

```sql
delete from alembic_version where version_num != '0006_add_vehicle_checkouts';
```

4) Rerun migrations:

```bash
python -m alembic upgrade head
```

### Checking if Site is Running

```bash
pa website get --domain username.pythonanywhere.com
```

Look for `enabled: True` in the output.

## ASGI Beta Limitations

> [!WARNING]
> ASGI deployment is experimental. Current limitations:
> - No static file mappings in the web UI
> - Limited web UI (request access via "Send feedback")
> - The site will not appear in the normal "Web" tab
> - Pricing may change in the future

## Environment Variables Reference

### Required

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | MySQL connection string |
| `FLASK_ENV` | Set to `production` |
| `INFLOW_API_KEY` | Inflow API key |
| `INFLOW_COMPANY_ID` | Inflow company ID |

### Authentication

| Variable | Description |
|----------|-------------|
| `SAML_ENABLED` | Enable SAML authentication |
| `SAML_IDP_ENTITY_ID` | IdP entity ID |
| `SAML_IDP_SSO_URL` | IdP login URL |
| `DEV_AUTH_BYPASS` | Development-only auth bypass for local testing |
| `DEV_AUTH_EMAIL` | Email for the synthetic local dev user |
| `DEV_AUTH_DISPLAY_NAME` | Display name for the synthetic local dev user |
| `DEV_AUTH_DEPARTMENT` | Optional department for the synthetic local dev user |
| `AZURE_TENANT_ID` | Azure tenant ID |
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_CLIENT_SECRET` | Service principal secret |

### Optional Features

| Variable | Description |
|----------|-------------|
| `SHAREPOINT_ENABLED` | Enable SharePoint integration |
| `SMTP_ENABLED` | Enable email notifications |
| `TEAMS_RECIPIENT_NOTIFICATIONS_ENABLED` | Enable Teams notifications |
