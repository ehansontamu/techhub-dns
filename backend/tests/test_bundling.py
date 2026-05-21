#!/usr/bin/env python3
"""Test script for document bundling functionality"""

import sys
import os
sys.path.append('..')

from pathlib import Path
import json

def test_qa_pdf_generation():
    """Test QA PDF generation (isolated)"""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    # Create temp directory
    temp_dir = Path("storage/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Create a mock order object
    class MockOrder:
        def __init__(self):
            self.inflow_order_id = "TEST123"
            self.recipient_name = "John Doe"
            self.id = "test-id"

    mock_order = MockOrder()

    # Sample QA data
    qa_data = {
        "method": "Delivery",
        "technician": "Test Technician",
        "qaSignature": "Test Signature",
        "verifyAssetTagSerialMatch": True,
        "verifyBoxesLabeledCorrectly": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True
    }

    # Generate QA PDF (copied from OrderService implementation)
    filename = f"{mock_order.inflow_order_id or mock_order.id}-qa.pdf"
    output_path = temp_dir / filename

    pdf = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter

    # Header
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, height - 50, "Quality Assurance Checklist")

    # Order details
    pdf.setFont("Helvetica", 12)
    y_pos = height - 80
    pdf.drawString(50, y_pos, f"Order: {mock_order.inflow_order_id}")
    pdf.drawString(50, y_pos - 20, f"Recipient: {mock_order.recipient_name or 'Unknown'}")
    pdf.drawString(50, y_pos - 40, f"Method: {qa_data.get('method', 'Unknown')}")
    pdf.drawString(50, y_pos - 60, f"Technician: {qa_data.get('technician', 'Unknown')}")

    # Checklist items
    y_pos -= 100
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y_pos, "Checklist Items:")
    y_pos -= 30

    pdf.setFont("Helvetica", 11)
    checklist_items = [
        ("verifyAssetTagSerialMatch", "Asset tags match serial numbers"),
        ("verifyBoxesLabeledCorrectly", "Boxes labeled correctly"),
        (
            "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved",
            "Order details template sent and electronic packing slip saved",
        ),
        ("verifyPackagedProperly", "Packaged properly"),
        ("verifyPackingSlipSerialsMatch", "Packing slip serials match")
    ]

    for field, description in checklist_items:
        status = "[PASS]" if qa_data.get(field, False) else "[FAIL]"
        pdf.drawString(70, y_pos, f"{status} {description}")
        y_pos -= 20

    # Signature line
    y_pos -= 40
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, y_pos, "QA Technician Signature:")
    pdf.line(50, y_pos - 10, width - 50, y_pos - 10)
    pdf.drawString(50, y_pos - 25, qa_data.get("qaSignature", ""))

    pdf.save()

    print(f"QA PDF generated: {output_path}")

    # Check if file exists
    if output_path.exists():
        print("[SUCCESS] QA PDF file created successfully")
        print(f"File size: {output_path.stat().st_size} bytes")
        return str(output_path)
    else:
        print("[FAILED] QA PDF file not found")
        return None

def test_pdf_bundling():
    """Test PDF bundling (isolated)"""
    from pypdf import PdfWriter

    # Create temp directory
    temp_dir = Path("storage/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Create test PDFs
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    # Create first test PDF
    pdf1_path = temp_dir / "test1.pdf"
    pdf1 = canvas.Canvas(str(pdf1_path), pagesize=letter)
    pdf1.drawString(50, 750, "Test Document 1 - Picklist")
    pdf1.drawString(50, 700, "Order: TEST123")
    pdf1.save()

    # Create second test PDF
    pdf2_path = temp_dir / "test2.pdf"
    pdf2 = canvas.Canvas(str(pdf2_path), pagesize=letter)
    pdf2.drawString(50, 750, "Test Document 2 - QA Form")
    pdf2.drawString(50, 700, "Quality Assurance Checklist")
    pdf2.save()

    # Test bundling
    output_path = temp_dir / "test-bundle.pdf"

    writer = PdfWriter()
    writer.append(str(pdf1_path))
    writer.append(str(pdf2_path))
    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    if output_path.exists():
        print(f"[SUCCESS] Bundled PDF created: {output_path}")
        print(f"File size: {output_path.stat().st_size} bytes")

        # Clean up
        pdf1_path.unlink(missing_ok=True)
        pdf2_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
    else:
        print("[FAILED] Bundled PDF not created")

if __name__ == "__main__":
    print("Testing document bundling functionality...")
    print()

    print("1. Testing QA PDF generation:")
    qa_path = test_qa_pdf_generation()
    print()

    print("2. Testing PDF bundling:")
    test_pdf_bundling()
    print()

    print("Test completed!")
