#!/bin/bash
# =============================================================================
# TechHub Delivery - Auto-Deploy Script
# =============================================================================
# This script is called by GitHub Actions (SSH) or manually to pull latest code
# and reload.
# Location: /home/techhub/techhub-dns/scripts/deploy.sh
#
# Usage: bash deploy.sh
# =============================================================================

set -e  # Exit on error

# Configuration
PROJECT_ROOT="${PROJECT_ROOT:-/home/techhub/techhub-dns}"
WSGI_FILE="${WSGI_FILE:-/var/www/techhub_pythonanywhere_com_wsgi.py}"
WEBAPP_DOMAIN="${WEBAPP_DOMAIN:-}"
BRANCH="${BRANCH:-main}"
LOG_FILE="${LOG_FILE:-${PROJECT_ROOT}/deploy.log}"
RUNNING_FILE="${RUNNING_FILE:-${PROJECT_ROOT}/.deploy.running}"
FRONTEND_DIR="${FRONTEND_DIR:-${PROJECT_ROOT}/frontend}"
LOCKFILE="${LOCKFILE:-${PROJECT_ROOT}/frontend/package-lock.json}"
LOCKFILE_HASH_FILE="${LOCKFILE_HASH_FILE:-${FRONTEND_DIR}/.deploy-lockfile.sha256}"
FRONTEND_DIST_DIR="${FRONTEND_DIST_DIR:-${FRONTEND_DIR}/dist}"
DEPLOY_PREFLIGHT="${DEPLOY_PREFLIGHT:-1}"
RELOAD_STRICT="${RELOAD_STRICT:-1}"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

handle_reload_failure() {
    local context="$1"

    if [ "$RELOAD_STRICT" = "1" ]; then
        log "ERROR: ${context}; RELOAD_STRICT=1 so failing deploy"
        exit 1
    fi

    log "WARNING: ${context}; RELOAD_STRICT=0 so deploy continuing"
}

reload_with_wsgi_touch() {
    if [ -f "$WSGI_FILE" ]; then
        touch "$WSGI_FILE"
        log "Touched WSGI file - app will reload"
    else
        log "WARNING: WSGI file not found at $WSGI_FILE"
    fi
}

reload_with_domain_wsgi_touch() {
    local domain="$1"
    local hyphen_preserving
    local hyphen_normalized
    local wsgi_candidate_primary
    local wsgi_candidate_fallback

    hyphen_preserving="${domain//./_}"
    hyphen_normalized="${hyphen_preserving//-/_}"
    wsgi_candidate_primary="/var/www/${hyphen_preserving}_wsgi.py"
    wsgi_candidate_fallback="/var/www/${hyphen_normalized}_wsgi.py"

    if [ -f "$wsgi_candidate_primary" ]; then
        touch "$wsgi_candidate_primary"
        log "Touched WSGI file - app will reload (domain=$domain)"
        return 0
    fi

    if [ "$wsgi_candidate_fallback" != "$wsgi_candidate_primary" ] && [ -f "$wsgi_candidate_fallback" ]; then
        touch "$wsgi_candidate_fallback"
        log "Touched WSGI file - app will reload (domain=$domain)"
        return 0
    fi

    if [ "$wsgi_candidate_fallback" = "$wsgi_candidate_primary" ]; then
        log "WARNING: WSGI file not found for domain=$domain at $wsgi_candidate_primary; skipping WSGI touch"
    else
        log "WARNING: WSGI file not found for domain=$domain at $wsgi_candidate_primary or $wsgi_candidate_fallback; skipping WSGI touch"
    fi

    return 1
}

reload_with_domain_fallbacks() {
    local domain="$1"
    local wsgi_default="/var/www/techhub_pythonanywhere_com_wsgi.py"
    local should_ignore_wsgi_file=0

    if [ -n "$domain" ] && [ "$WSGI_FILE" = "$wsgi_default" ]; then
        should_ignore_wsgi_file=1
    fi

    if [ "$should_ignore_wsgi_file" -eq 0 ] && [ -n "$WSGI_FILE" ] && [ -f "$WSGI_FILE" ]; then
        touch "$WSGI_FILE"
        log "Touched WSGI file - app will reload"
        return 0
    fi

    if [ -n "$WSGI_FILE" ] && [ "$should_ignore_wsgi_file" -eq 1 ]; then
        log "WARNING: WSGI_FILE ignored for domain=$domain (default path); attempting domain-specific WSGI touch"
    elif [ -n "$WSGI_FILE" ]; then
        log "WARNING: WSGI_FILE set but not found at $WSGI_FILE; attempting domain-specific WSGI touch"
    else
        log "WARNING: WSGI_FILE not set; attempting domain-specific WSGI touch"
    fi

    if [ -n "$domain" ] && reload_with_domain_wsgi_touch "$domain"; then
        return 0
    fi

    if [ "$should_ignore_wsgi_file" -eq 1 ]; then
        log "WARNING: WSGI_FILE ignored for domain=$domain (default path); no WSGI touch performed"
        return 1
    fi

    if [ -n "$WSGI_FILE" ] && [ -f "$WSGI_FILE" ]; then
        touch "$WSGI_FILE"
        log "Touched WSGI file - app will reload (fallback)"
        return 0
    fi

    if [ -n "$WSGI_FILE" ]; then
        log "WARNING: WSGI_FILE not found at $WSGI_FILE; no WSGI touch performed"
    else
        log "WARNING: No WSGI file available for reload"
    fi

    return 1
}

run_non_blocking_preflight() {
    if [ "$DEPLOY_PREFLIGHT" = "0" ]; then
        log "Post-deploy preflight skipped (DEPLOY_PREFLIGHT=0)"
        return 0
    fi

    set +e
    log "Post-deploy preflight (non-blocking)"

    local current_branch current_commit frontend_dist env_file
    current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
    current_commit="$(git rev-parse --short HEAD 2>/dev/null)"
    log "- git_branch=${current_branch:-unknown} git_commit=${current_commit:-unknown}"
    log "- expected_project_root=$PROJECT_ROOT"

    frontend_dist="$PROJECT_ROOT/frontend/dist"
    if [ -d "$frontend_dist" ]; then
        log "- frontend_dist_exists=yes path=$frontend_dist"
    else
        log "- frontend_dist_exists=no path=$frontend_dist"
    fi

    env_file="$PROJECT_ROOT/backend/.env"
    if [ -f "$env_file" ]; then
        local key
        for key in DB_POOL_SIZE DB_MAX_OVERFLOW DB_POOL_TIMEOUT DB_POOL_RECYCLE SCHEDULER_ENABLED; do
            if grep -q "^${key}=" "$env_file"; then
                log "- env_key_present ${key}=yes"
            else
                log "- env_key_present ${key}=no"
            fi
        done
    else
        log "- backend_env_file_present=no path=$env_file"
    fi

    set -e
}

remove_node_modules_with_retries() {
    local max_attempts=5
    local attempt=1

    while [ "$attempt" -le "$max_attempts" ]; do
        rm -rf node_modules 2>/dev/null || true

        if [ ! -d node_modules ]; then
            return 0
        fi

        log "WARNING: node_modules cleanup attempt ${attempt}/${max_attempts} did not fully remove directory"
        sleep 2
        attempt=$((attempt + 1))
    done

    log "ERROR: Unable to remove node_modules after ${max_attempts} attempts"
    return 1
}

lockfile_checksum() {
    local file_path="$1"

    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file_path" | awk '{print $1}'
        return 0
    fi

    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file_path" | awk '{print $1}'
        return 0
    fi

    if command -v python3 >/dev/null 2>&1; then
        python3 - "$file_path" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
        return 0
    fi

    return 1
}

should_install_frontend_deps() {
    if [ ! -d node_modules ]; then
        log "Frontend node_modules missing; installing dependencies"
        return 0
    fi

    if [ ! -f "$LOCKFILE" ]; then
        log "WARNING: package-lock.json not found; installing dependencies"
        return 0
    fi

    if [ ! -f "$LOCKFILE_HASH_FILE" ]; then
        log "Frontend lockfile fingerprint missing; installing dependencies"
        return 0
    fi

    local current_lockfile_hash
    local previous_lockfile_hash

    current_lockfile_hash="$(lockfile_checksum "$LOCKFILE" 2>/dev/null || true)"
    previous_lockfile_hash="$(cat "$LOCKFILE_HASH_FILE" 2>/dev/null || true)"

    if [ -z "$current_lockfile_hash" ]; then
        log "WARNING: Unable to compute lockfile checksum; installing dependencies"
        return 0
    fi

    if [ "$current_lockfile_hash" != "$previous_lockfile_hash" ]; then
        log "Frontend lockfile changed; installing dependencies"
        return 0
    fi

    log "Frontend dependencies unchanged; skipping npm install"
    return 1
}

save_lockfile_fingerprint() {
    if [ ! -f "$LOCKFILE" ]; then
        return 0
    fi

    local lockfile_hash
    lockfile_hash="$(lockfile_checksum "$LOCKFILE" 2>/dev/null || true)"

    if [ -n "$lockfile_hash" ]; then
        printf '%s\n' "$lockfile_hash" > "$LOCKFILE_HASH_FILE"
    fi
}

run_backend_migrations() {
    local backend_dir="${PROJECT_ROOT}/backend"
    local -a alembic_cmd

    if [ ! -d "$backend_dir" ]; then
        log "ERROR: Backend directory not found at $backend_dir"
        return 1
    fi

    if [ -x "${backend_dir}/.venv/bin/alembic" ]; then
        alembic_cmd=("${backend_dir}/.venv/bin/alembic")
    elif command -v alembic >/dev/null 2>&1; then
        alembic_cmd=("alembic")
    elif command -v python3 >/dev/null 2>&1; then
        alembic_cmd=("python3" "-m" "alembic")
    else
        log "ERROR: Alembic is unavailable; cannot run database migrations"
        return 1
    fi

    log "Running database migrations (alembic upgrade head)..."
    cd "$backend_dir"
    if ! "${alembic_cmd[@]}" upgrade head; then
        log "ERROR: Database migration failed"
        return 1
    fi
    cd "$PROJECT_ROOT"
    log "Database migrations complete"
# Pre-deploy import check: verify the app can be imported on the server using the project venv
if [ "$SKIP_IMPORT_CHECK" != "1" ]; then
    log "Pre-deploy import check: testing 'import app' with project virtualenv..."
    backend_dir="${PROJECT_ROOT}/backend"
    if [ -d "$backend_dir/.venv" ]; then
        python_cmd="$backend_dir/.venv/bin/python"
    else
        python_cmd="python3"
    fi
    import_output=$($python_cmd -c "import app" 2>&1) || import_rc=$?
    if [ $? -ne 0 ]; then
        log "ERROR: Remote import failed! Traceback:\n$import_output"
        log "Aborting deployment due to import failure (RELOAD_STRICT=$RELOAD_STRICT)"
        exit 1
    else
        log "Pre-deploy import check passed."
    fi
fi
}

install_frontend_deps() {
    local -a install_cmd
    local -a fallback_cmd

    if [ -f "$LOCKFILE" ]; then
        log "Installing frontend dependencies (npm ci)..."
        install_cmd=(npm ci --include=dev --no-audit --fund=false)
        fallback_cmd=(npm install --include=dev --no-audit --fund=false)
    else
        log "WARNING: package-lock.json not found; running npm install"
        install_cmd=(npm install --include=dev --no-audit --fund=false)
    fi

    set +e
    "${install_cmd[@]}"
    local first_attempt_status=$?
    set -e

    if [ "$first_attempt_status" -eq 0 ]; then
        save_lockfile_fingerprint
        return 0
    fi

    if [ "${#fallback_cmd[@]}" -gt 0 ]; then
        log "WARNING: npm ci failed; attempting npm install without cleanup"
        set +e
        "${fallback_cmd[@]}"
        local fallback_status=$?
        set -e

        if [ "$fallback_status" -eq 0 ]; then
            log "npm install succeeded after npm ci failure"
            save_lockfile_fingerprint
            return 0
        fi

        log "WARNING: npm install failed after npm ci failure; attempting cleanup"
        if ! remove_node_modules_with_retries; then
            log "ERROR: Cleanup failed after npm ci/npm install failures; aborting deploy"
            return 1
        fi

        log "Retrying npm install after cleanup..."
        set +e
        "${fallback_cmd[@]}"
        local retry_status=$?
        set -e

        if [ "$retry_status" -ne 0 ]; then
            log "ERROR: Frontend dependency install failed after cleanup retry"
            return 1
        fi

        save_lockfile_fingerprint
        return 0
    fi

    log "WARNING: Frontend dependency install failed; cleaning node_modules and retrying once"
    if ! remove_node_modules_with_retries; then
        log "ERROR: Cleanup failed after npm install failure; aborting deploy"
        return 1
    fi

    log "Retrying frontend dependency install..."
    set +e
    "${install_cmd[@]}"
    local second_attempt_status=$?
    set -e

    if [ "$second_attempt_status" -ne 0 ]; then
        log "ERROR: Frontend dependency install failed after cleanup retry"
        return 1
    fi

    save_lockfile_fingerprint
}

# Deploy lock (prevents concurrent deploys)
if [ -f "$RUNNING_FILE" ]; then
    existing_pid="$(cat "$RUNNING_FILE" 2>/dev/null || true)"

    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" >/dev/null 2>&1; then
        log "ERROR: Deploy already running (pid=$existing_pid); exiting"
        exit 1
    fi

    # Stale/invalid marker
    rm -f "$RUNNING_FILE"
fi

# Create marker atomically (avoid race between two webhook deliveries)
if ! ( set -o noclobber; echo "$$" > "$RUNNING_FILE" ) 2>/dev/null; then
    existing_pid="$(cat "$RUNNING_FILE" 2>/dev/null || true)"
    log "ERROR: Deploy already running (pid=${existing_pid:-unknown}); exiting"
    exit 1
fi

trap 'rm -f "$RUNNING_FILE"' EXIT

# Ensure npm is available in non-interactive shells
if ! command -v npm >/dev/null 2>&1; then
    [ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
    [ -f "$HOME/.profile" ] && . "$HOME/.profile"
    export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
fi

if ! command -v npm >/dev/null 2>&1; then
    log "ERROR: npm not found in PATH; cannot build frontend"
    exit 1
fi

# Start deployment
log "=========================================="
log "Starting deployment..."
log "=========================================="
log "Reload strictness: RELOAD_STRICT=${RELOAD_STRICT}"

# Navigate to project directory
cd "$PROJECT_ROOT"
log "Changed to $PROJECT_ROOT"

# Fetch and pull latest changes
log "Pulling latest changes from origin/$BRANCH..."
previous_commit="$(git rev-parse HEAD 2>/dev/null || true)"
git fetch origin "$BRANCH"
target_commit="$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)"

frontend_changed=1
if [ -n "$previous_commit" ] && [ -n "$target_commit" ] && [ "$previous_commit" != "$target_commit" ]; then
    if git diff --quiet "$previous_commit" "$target_commit" -- frontend; then
        frontend_changed=0
    fi
fi

git reset --hard "origin/$BRANCH"
log "Git pull complete"

# Clean up deprecated local document storage — now using SharePoint exclusively
if [ -d "${PROJECT_ROOT}/storage" ]; then
    log "Removing legacy local storage directory..."
    rm -rf "${PROJECT_ROOT}/storage"
    log "Legacy storage removed"
fi

if ! run_backend_migrations; then
    exit 1
fi

# Build frontend on PythonAnywhere
if [ -d "$FRONTEND_DIR" ]; then
    should_build_frontend=1
    if [ "$frontend_changed" -eq 0 ] && [ -d "$FRONTEND_DIST_DIR" ]; then
        should_build_frontend=0
    fi

    if [ "$should_build_frontend" -eq 1 ]; then
        log "Building frontend..."
        cd "$FRONTEND_DIR"

        if should_install_frontend_deps; then
            install_frontend_deps
        fi

        log "Running frontend build..."
        npm run build
        log "Frontend build complete"
        cd "$PROJECT_ROOT"
    else
        log "No frontend changes detected and dist exists; skipping frontend build"
    fi
else
    log "WARNING: Frontend directory not found at $FRONTEND_DIR"
fi

# Show what changed
log "Recent commits:"
git log --oneline -3




# Debug: list /var/www to discover WSGI filename pattern
if [ -n "$WEBAPP_DOMAIN" ]; then
    log "DEBUG: Listing /var/www for wsgi files..."
    ls -la /var/www/*.py 2>&1 | tee -a "$LOG_FILE" || true
    # Also try to find files matching the domain
    log "DEBUG: Looking for files matching *$(echo "$WEBAPP_DOMAIN" | tr '.' '_')*.py"
    find /var/www -maxdepth 1 -name "*wsgi*.py" 2>&1 | tee -a "$LOG_FILE" || true
fi

    log "DEBUG: backend_dir=${backend_dir}"
    log "DEBUG: Listing ${backend_dir}:"
    ls -la "${backend_dir}" 2>&1 | tee -a "$LOG_FILE" || true
    log "DEBUG: end listing"
    # Migration: rename venv to .venv if .venv missing (PA web command expects .venv)
    if [ -d "${backend_dir}/venv" ] && [ ! -d "${backend_dir}/.venv" ]; then
        log "Renaming virtualenv: venv/ -> .venv/ (to match PA web app command)"
        mv "${backend_dir}/venv" "${backend_dir}/.venv" || {
            log "WARNING: Failed to rename venv to .venv; will try creating .venv fresh"
            rm -rf "${backend_dir}/.venv"
            mv "${backend_dir}/venv" "${backend_dir}/.venv"
        }
    fi
# Reload web app (prefer PythonAnywhere CLI when available)
    if [ -n "$WEBAPP_DOMAIN" ] && command -v pa >/dev/null 2>&1; then
        if pa website reload --domain "$WEBAPP_DOMAIN"; then
            log "Reloaded app via PythonAnywhere CLI for domain: $WEBAPP_DOMAIN"
        else
            log "WARNING: PythonAnywhere CLI reload failed for domain: $WEBAPP_DOMAIN; attempting WSGI touch fallback"
            if ! reload_with_domain_fallbacks "$WEBAPP_DOMAIN"; then
                handle_reload_failure "WSGI reload failed after PythonAnywhere CLI reload failure (domain=$WEBAPP_DOMAIN)"
            fi
    fi

    else
        if [ -z "$WEBAPP_DOMAIN" ]; then
            log "WEBAPP_DOMAIN not set; using WSGI touch fallback"
            reload_with_wsgi_touch
        else
            log "WARNING: WEBAPP_DOMAIN set to $WEBAPP_DOMAIN but PythonAnywhere CLI (pa) not found; attempting WSGI touch fallback"
            if ! reload_with_domain_fallbacks "$WEBAPP_DOMAIN"; then
                handle_reload_failure "WSGI reload failed without PythonAnywhere CLI (domain=$WEBAPP_DOMAIN)"
            fi
        fi
    fi

log "=========================================="
log "Deployment complete!"

# Post-reload: fetch recent error log entries for debugging
log "Fetching recent PA error log entries (up to 50 lines)..."
tail -n 50 "$HOME/.pythonanywhere/error.log" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Could not read PA error log (maybe not accessible)"
log "=========================================="

run_non_blocking_preflight