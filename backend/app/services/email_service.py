"""
Email Service using Microsoft Graph API.

Sends emails via Microsoft Graph API using Service Principal authentication.
Replaces the previous SMTP relay approach for unified Microsoft integration.

Requires Azure AD app with Mail.Send permission.
"""

import logging
from typing import Optional

from app.config import settings
from app.services.graph_service import graph_service

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via Microsoft Graph API."""

    def __init__(self):
        self.from_name = settings.email_from_name
        self.from_address = settings.smtp_from_address or "techhub@tamu.edu"

    @property
    def is_enabled(self) -> bool:
        """Check if email sending is enabled in system settings."""
        from app.services.system_setting_service import (
            SETTING_EMAIL_ENABLED,
            SystemSettingService,
        )

        return SystemSettingService.is_setting_enabled(SETTING_EMAIL_ENABLED)

    def is_configured(self) -> bool:
        """Check if email is properly configured (Graph API must be configured).

        Note: graph_service.send_email() already falls back to techhub@tamu.edu
        when smtp_from_address is not set, so we don't gate on from_address here.
        """
        return bool(graph_service.is_configured())

    def send_email(
        self,
        to_address: str,
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachment_name: Optional[str] = None,
        attachment_content: Optional[bytes] = None,
        attachment_type: str = "application/pdf",
        force: bool = False,
    ) -> bool:
        """
        Send an email via Microsoft Graph API.

        Args:
            to_address: Recipient email address
            subject: Email subject
            body_html: HTML body content
            body_text: Optional plain text body (kept for compatibility, not used by Graph)
            attachment_name: Optional attachment filename
            attachment_content: Optional attachment content as bytes
            attachment_type: MIME type of attachment (kept for compatibility)
            force: If True, bypass the enabled check (for testing)

        Returns:
            True if email sent successfully, False otherwise
        """
        if not force and not self.is_enabled:
            logger.info("Email sending is disabled in System Settings")
            return False

        if not self.is_configured():
            logger.warning(
                "Email not configured. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and SMTP_FROM_ADDRESS in .env"
            )
            return False

        return graph_service.send_email(
            to_address=to_address,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_address=self.from_address,
            from_name=self.from_name,
            attachment_name=attachment_name,
            attachment_content=attachment_content,
            initiated_by="email_service",
        )

    def send_order_details_email(
        self,
        to_address: str,
        order_number: str,
        customer_name: str,
        pdf_content: bytes,
        force: bool = False,
    ) -> bool:
        """
        Send Order Details PDF email to recipient.

        Args:
            to_address: Recipient email address
            order_number: The order number (e.g., TH4013)
            customer_name: Customer's display name
            pdf_content: Order Details PDF as bytes
            force: If True, bypass the enabled check (for testing)

        Returns:
            True if email sent successfully, False otherwise
        """
        subject = f"TechHub Order Details - {order_number}"

        body_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #500000;">Your TechHub Order Details</h2>
            <p>Dear {customer_name},</p>
            <p>Thank you for your order with TechHub Technology Services.</p>
            <p>Please find attached your <strong>Order Details</strong> document for order <strong>{order_number}</strong>.</p>
            <p><em>Important:</em> Do not edit preliminary asset information. Editing preliminary asset information may result in items on your order arriving without asset tags.</p>
            <hr style="border: 1px solid #ddd; margin: 20px 0;">
            <p style="font-size: 12px; color: #666;">
                This is an automated message from TechHub Technology Services.<br>
                WCDC - TechHub | 474 Agronomy Rd | College Station, TX 77843
            </p>
        </body>
        </html>
        """

        body_text = f"""
Your TechHub Order Details

Howdy {customer_name},

Thank you for your order with TechHub Technology Services.

Please find attached your Order Details document for order {order_number}.

Important: Do not edit preliminary asset information.

---
TechHub Technology Services
WCDC - TechHub | 474 Agronomy Rd | College Station, TX 77843
        """

        return self.send_email(
            to_address=to_address,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            attachment_name=f"OrderDetails_{order_number}.pdf",
            attachment_content=pdf_content,
            force=force,
        )


# Singleton instance
email_service = EmailService()
