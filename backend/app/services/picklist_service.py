"""
Picklist Service for generating picklist PDFs.

Extracted from OrderService for better separation of concerns.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

from app.utils.pdf_helpers import wrap_text, check_page_break, filter_picklines

logger = logging.getLogger(__name__)


class PicklistService:
    """Service for generating picklist PDFs from inFlow order data."""

    def __init__(self):
        self.logo_path = (
            Path(__file__).parent.parent / "templates" / "tamu_tech_services.png"
        )

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _as_text_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    def generate_picklist_pdf(
        self, inflow_data: Dict[str, Any], output_path: str
    ) -> None:
        """Generate a picklist PDF from inFlow order data."""
        # Extract order data
        po_number = self._as_text(inflow_data.get("poNumber", ""))
        pick_lines = inflow_data.get("pickLines", [])
        customer_name = self._as_text(inflow_data.get("contactName", ""))
        email = self._as_text(inflow_data.get("email", ""))
        order_number = self._as_text(inflow_data.get("orderNumber", ""))
        shipping_address_data = self._as_dict(inflow_data.get("shippingAddress"))
        shipping_address = self._as_text(shipping_address_data.get("address1", ""))
        order_remarks = self._as_text(inflow_data.get("orderRemarks", ""))

        # Filter pick lines (remove already shipped items)
        pick_lines = filter_picklines(inflow_data, pick_lines)

        # Create PDF
        pdf = canvas.Canvas(output_path, pagesize=letter)
        width, height = letter

        # Set PDF title
        pdf.setTitle(f"PO Number: {po_number}")
        pdf.setFont("Helvetica", 10)

        # Header
        x_offset = 50
        logo_width = 150
        logo_height = 50
        logo_bottom_margin = 20
        logo_top_y = height - logo_height - logo_bottom_margin
        pdf.drawImage(
            str(self.logo_path),
            x_offset,
            logo_top_y,
            width=logo_width,
            height=logo_height,
            preserveAspectRatio=True,
            mask="auto",
        )
        y_offset = logo_top_y - 20

        pdf.drawString(x_offset, y_offset, f"WCDC - TechHub")
        pdf.drawRightString(width - x_offset, y_offset, f"Customer: {customer_name}")
        y_offset -= 15
        pdf.drawString(x_offset, y_offset, f"474 Agronomy Rd")
        pdf.drawRightString(width - x_offset, y_offset, f"Email: {email}")
        y_offset -= 15
        pdf.drawString(x_offset, y_offset, f"College Station, TX")
        pdf.drawRightString(width - x_offset, y_offset, f"PO Number: {po_number}")
        y_offset -= 15
        pdf.drawString(x_offset, y_offset, f"77843 USA")
        pdf.drawRightString(
            width - x_offset, y_offset, f"Shipping Address: {shipping_address}"
        )
        y_offset -= 15

        # Add Recipient UIN(s) or Name(s)
        custom_fields = self._as_dict(inflow_data.get("customFields"))
        recipient_info = self._as_text(custom_fields.get("custom4", ""))
        pdf.drawRightString(
            width - x_offset, y_offset, f"Recipient UIN(s) or Name(s): {recipient_info}"
        )
        y_offset -= 15
        pdf.line(x_offset, y_offset - 5, x_offset + 500, y_offset - 5)
        y_offset -= 25

        # Order Header
        pdf.setFont("Times-Bold", 16)
        pdf.drawString(x_offset, y_offset, f"Order Number: {order_number}")
        y_offset -= 25

        # Items section
        pdf.setFont("Times-Bold", 14)
        pdf.drawString(x_offset, y_offset, "Items:")
        y_offset -= 25
        pdf.line(x_offset, y_offset + 15, x_offset + 500, y_offset + 15)

        pdf.setFont("Helvetica", 12)

        for item in pick_lines:
            product = self._as_dict(item.get("product"))
            item_name = self._as_text(product.get("name", "")).upper()
            sku = self._as_text(product.get("sku", ""))
            quantity = self._as_dict(item.get("quantity"))
            standard_quantity = self._as_text(quantity.get("standardQuantity", ""))
            serial_numbers = self._as_text_list(quantity.get("serialNumbers", []))

            # Product name and quantity
            pdf.setFont("Helvetica-Oblique", 11)
            pdf.drawString(x_offset, y_offset, f"{item_name} (SKU: {sku})")
            pdf.drawRightString(
                width - x_offset,
                y_offset,
                f"{standard_quantity.replace('.0', '')} item(s)",
            )
            y_offset -= 20

            y_offset = check_page_break(pdf, y_offset, height)

            # Serial numbers
            if serial_numbers:
                serial_text = "Serial Numbers: " + ", ".join(serial_numbers)
                max_width = width - x_offset - 50

                text_object = pdf.beginText(x_offset, y_offset)
                text_object.setFont("Helvetica-Bold", 11)

                words = serial_text.split(" ")
                current_line = ""
                for word in words:
                    if (
                        pdf.stringWidth(current_line + word, "Helvetica", 11)
                        < max_width
                    ):
                        current_line += word + " "
                    else:
                        text_object.textLine(current_line.strip())
                        current_line = word + " "
                        y_offset -= 15
                        y_offset = check_page_break(pdf, y_offset, height)
                        if y_offset == height - 50:  # Page break occurred
                            text_object = pdf.beginText(x_offset, y_offset)
                            text_object.setFont("Helvetica-Bold", 11)

                if current_line:
                    text_object.textLine(current_line.strip())
                    y_offset -= 20

                pdf.drawText(text_object)

            pdf.line(x_offset, y_offset + 15, x_offset + 500, y_offset + 15)
            y_offset -= 5

        # Order Remarks
        y_offset -= 20
        y_offset = check_page_break(pdf, y_offset, height)

        pdf.setFont("Times-Bold", 14)
        pdf.drawString(x_offset, y_offset, "Order Remarks:")
        y_offset -= 20
        pdf.line(x_offset, y_offset + 15, x_offset + 500, y_offset + 15)

        y_offset = check_page_break(pdf, y_offset, height)

        pdf.setFont("Helvetica-Bold", 11)
        wrapped_lines = wrap_text(order_remarks, 500, "Helvetica-Bold", 11)
        for line in wrapped_lines:
            if y_offset < 60:
                pdf.showPage()
                y_offset = height - 50
                pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(x_offset, y_offset, line)
            y_offset -= 14

        pdf.save()
        logger.info(f"Picklist PDF generated: {output_path}")


# Singleton for easy import
picklist_service = PicklistService()
