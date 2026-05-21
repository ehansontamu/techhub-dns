"""
SAML Authentication Service.

Handles SAML response parsing, user creation/update, and session management.
Uses python3-saml library for SAML protocol handling.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.models.user import User
from app.models.session import Session
from app.utils.exceptions import DNSApiError
from app.services.graph_service import graph_service

logger = logging.getLogger(__name__)


class SamlAuthService:
    """Service for handling SAML authentication with TAMU Entra ID."""

    def __init__(self):
        self._settings_cache = None

    def is_configured(self) -> bool:
        """Check if SAML is properly configured."""
        return bool(
            settings.saml_enabled and
            settings.saml_idp_entity_id and
            settings.saml_idp_sso_url and
            settings.saml_idp_cert_path
        )

    def get_saml_settings(self) -> dict:
        """
        Generate python3-saml settings dict from app configuration.

        Returns:
            dict: Settings for OneLogin_Saml2_Auth
        """
        if self._settings_cache:
            return self._settings_cache

        if not self.is_configured():
            raise DNSApiError(
                code="SAML_CONFIG_ERROR",
                message="SAML is not fully configured",
                status_code=503,
                details={"reason": "missing_required_settings"},
            )

        # Read IdP certificate
        cert_path = self._resolve_cert_path(settings.saml_idp_cert_path)
        if not cert_path:
            raise DNSApiError(
                code="SAML_CONFIG_ERROR",
                message="SAML IdP certificate file not found",
                status_code=503,
                details={
                    "reason": "cert_not_found",
                    "cert_path": settings.saml_idp_cert_path,
                },
            )

        try:
            with open(cert_path, "r", encoding="utf-8") as cert_file:
                idp_cert = cert_file.read().strip()
        except OSError as error:
            logger.error("Failed reading SAML certificate at %s: %s", cert_path, error)
            raise DNSApiError(
                code="SAML_CONFIG_ERROR",
                message="SAML IdP certificate file is unreadable",
                status_code=503,
                details={"reason": "cert_unreadable", "cert_path": cert_path},
            ) from error

        if not self._is_valid_pem_certificate(idp_cert):
            raise DNSApiError(
                code="SAML_CONFIG_ERROR",
                message="SAML IdP certificate file is invalid",
                status_code=503,
                details={"reason": "cert_invalid_pem", "cert_path": cert_path},
            )

        # Parse ACS URL for SP settings
        acs_parsed = urlparse(settings.saml_acs_url)

        self._settings_cache = {
            "strict": True,
            "debug": settings.flask_env == "development",
            "sp": {
                "entityId": settings.saml_sp_entity_id,
                "assertionConsumerService": {
                    "url": settings.saml_acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "idp": {
                "entityId": settings.saml_idp_entity_id,
                "singleSignOnService": {
                    "url": settings.saml_idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                },
                "x509cert": idp_cert,
            },
            "security": {
                "authnRequestsSigned": False,
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "wantNameIdEncrypted": False,
            }
        }
        return self._settings_cache

    def _resolve_cert_path(self, configured_path: Optional[str]) -> Optional[str]:
        if not configured_path:
            return None

        candidate_paths = []
        if os.path.isabs(configured_path):
            candidate_paths.append(configured_path)
        else:
            backend_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            repo_root = os.path.dirname(backend_root)
            candidate_paths.extend(
                [
                    configured_path,
                    os.path.join(backend_root, configured_path),
                    os.path.join(repo_root, configured_path),
                ]
            )

        for path in candidate_paths:
            if os.path.exists(path):
                if path != configured_path:
                    logger.info("Resolved SAML cert path to: %s", path)
                return path

        logger.error(
            "SAML certificate not found at configured path '%s' (cwd: %s)",
            configured_path,
            os.getcwd(),
        )
        return None

    def _is_valid_pem_certificate(self, certificate_contents: str) -> bool:
        return (
            "-----BEGIN CERTIFICATE-----" in certificate_contents
            and "-----END CERTIFICATE-----" in certificate_contents
        )

    def get_or_create_user(
        self,
        db: DbSession,
        saml_attributes: dict
    ) -> User:
        """
        Get existing user or create new one from SAML attributes.

        Args:
            db: Database session
            saml_attributes: Dict of SAML assertion attributes

        Returns:
            User object (new or existing)
        """
        # Extract attributes from SAML response.
        # Azure/Entra can surface either the standard claim URIs or custom
        # short claim names depending on how the SAML app is configured.
        oid = self._get_attr(
            saml_attributes,
            'http://schemas.microsoft.com/identity/claims/objectidentifier',
            'oid',
            'objectidentifier',
        )
        email = self._get_attr(
            saml_attributes,
            'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
            'email',
            'userprincipalname',
            'upn',
        )
        display_name = self._get_attr(
            saml_attributes,
            'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name',
            'http://schemas.microsoft.com/identity/claims/displayname',
            'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/displayname',
            'display_name',
            'displayname',
            'name',
        )
        given_name = self._get_attr(
            saml_attributes,
            'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname',
            'given_name',
            'givenname',
        )
        surname = self._get_attr(
            saml_attributes,
            'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname',
            'surname',
            'family_name',
            'familyname',
        )
        department = self._get_attr(saml_attributes, 'department')
        employee_id = self._get_attr(saml_attributes, 'employeeid', 'employee_id')

        # Fallback: construct display name from given name + surname if not provided
        if not display_name and (given_name or surname):
            display_name = f"{given_name or ''} {surname or ''}".strip()

        # =====================================================================
        # Enrich from Microsoft Graph API (source of truth for directory data)
        # =====================================================================
        graph_profile = None
        if oid:
            try:
                graph_profile = graph_service.get_user_profile(oid)
            except Exception:
                logger.exception("Graph user profile lookup failed; continuing with SAML attributes only")
                graph_profile = None

            if graph_profile:
                graph_display_name = graph_profile.get("displayName")
                graph_given_name = graph_profile.get("givenName")
                graph_surname = graph_profile.get("surname")
                graph_department = graph_profile.get("department")
                graph_employee_id = graph_profile.get("employeeId")

                if graph_display_name:
                    display_name = graph_display_name
                    logger.info(f"Overrode SAML display_name with Graph displayName for {email}")
                if graph_given_name:
                    given_name = graph_given_name
                if graph_surname:
                    surname = graph_surname
                if graph_department:
                    department = graph_department
                if graph_employee_id:
                    employee_id = graph_employee_id

        if not oid:
            raise ValueError("SAML response missing required 'objectidentifier' claim")
        if not email:
            raise ValueError("SAML response missing required 'emailaddress' claim")

        # Look up existing user by OID
        user = db.query(User).filter(User.tamu_oid == oid).first()

        if user:
            # Update user info on login.
            user.email = email
            if display_name is not None:
                user.display_name = display_name
            if department is not None:
                user.department = department
            if employee_id is not None:
                user.employee_id = employee_id
            user.last_login_at = datetime.utcnow()
            logger.info(f"User login: {email}")
        else:
            # Create new user
            user = User(
                tamu_oid=oid,
                email=email,
                display_name=display_name,
                department=department,
                employee_id=employee_id,
                created_at=datetime.utcnow(),
                last_login_at=datetime.utcnow(),
            )
            db.add(user)
            logger.info(f"New user created: {email}")

        db.commit()
        db.refresh(user)
        return user

    def build_user_attributes_from_oidc_claims(self, oidc_claims: dict) -> dict:
        """Normalize Microsoft Entra OIDC claims into the shared user-attribute shape."""
        oid = self._get_attr(
            oidc_claims,
            "oid",
            "objectid",
        )
        email = self._get_attr(
            oidc_claims,
            "preferred_username",
            "email",
            "upn",
            "userprincipalname",
        )
        display_name = self._get_attr(
            oidc_claims,
            "name",
            "display_name",
            "displayname",
        )
        given_name = self._get_attr(
            oidc_claims,
            "given_name",
            "givenname",
        )
        surname = self._get_attr(
            oidc_claims,
            "family_name",
            "surname",
        )
        department = self._get_attr(oidc_claims, "department")
        employee_id = self._get_attr(oidc_claims, "employeeid", "employee_id")

        return {
            "oid": oid,
            "email": email,
            "display_name": display_name,
            "given_name": given_name,
            "surname": surname,
            "department": department,
            "employeeid": employee_id,
        }

    def get_or_create_user_from_oidc_claims(
        self,
        db: DbSession,
        oidc_claims: dict,
    ) -> User:
        """Create or update a TAMU user from Microsoft Entra OIDC claims."""
        return self.get_or_create_user(
            db,
            self.build_user_attributes_from_oidc_claims(oidc_claims),
        )

    def _get_attr(self, attributes: dict, *keys: str) -> Optional[str]:
        """Extract the first single-value attribute found for any candidate key."""
        for key in keys:
            value = attributes.get(key)
            if isinstance(value, list) and len(value) > 0:
                return value[0]
            if isinstance(value, str):
                return value
        return None

    def create_session(
        self,
        db: DbSession,
        user: User,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Session:
        """
        Create a new session for the user.

        Args:
            db: Database session
            user: Authenticated user
            user_agent: Browser user agent string
            ip_address: Client IP address

        Returns:
            New Session object
        """
        session = Session(
            user_id=user.id,
            user_agent=user_agent[:500] if user_agent and len(user_agent) > 500 else user_agent,
            ip_address=ip_address,
            expires_at=datetime.utcnow() + timedelta(hours=settings.session_max_age_hours),
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        logger.info(f"Session created for user {user.email}")
        return session

    def validate_session(
        self,
        db: DbSession,
        session_id: str
    ) -> Optional[Tuple[User, Session]]:
        """
        Validate a session ID and return user + session if valid.

        Args:
            db: Database session
            session_id: Session ID from cookie

        Returns:
            Tuple of (User, Session) if valid, None otherwise
        """
        if not session_id:
            return None

        session = db.query(Session).filter(Session.id == session_id).first()
        if not session or not session.is_valid():
            return None

        user = db.query(User).filter(User.id == session.user_id).first()
        if not user:
            return None

        # Update last_seen_at for activity tracking
        session.last_seen_at = datetime.utcnow()
        db.commit()

        # Refresh objects to ensure they are not expired by the commit
        db.refresh(user)
        db.refresh(session)

        return (user, session)

    def revoke_session(self, db: DbSession, session_id: str) -> bool:
        """Revoke a specific session."""
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.revoked_at = datetime.utcnow()
            db.commit()
            logger.info(f"Session revoked: {session_id}")
            return True
        return False

    def revoke_all_sessions(self, db: DbSession, user_id: str, except_session_id: Optional[str] = None) -> int:
        """Revoke all sessions for a user, optionally keeping one."""
        query = db.query(Session).filter(
            Session.user_id == user_id,
            Session.revoked_at.is_(None)
        )
        if except_session_id:
            query = query.filter(Session.id != except_session_id)

        sessions = query.all()
        now = datetime.utcnow()
        for session in sessions:
            session.revoked_at = now

        db.commit()
        logger.info(f"Revoked {len(sessions)} sessions for user {user_id}")
        return len(sessions)

    def get_user_sessions(self, db: DbSession, user_id: str) -> list:
        """Get all active sessions for a user."""
        now = datetime.utcnow()
        return db.query(Session).filter(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        ).order_by(Session.created_at.desc()).all()


# Singleton instance
saml_auth_service = SamlAuthService()
