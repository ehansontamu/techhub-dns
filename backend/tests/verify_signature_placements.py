#!/usr/bin/env python3
"""Test signature placement logic"""

import sys
import os
import base64
import io
from pathlib import Path

# Add backend to path and provide a lightweight SQLite database URL for imports.
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/techhub-signature-smoke.db")

from app.services.order_service import OrderService
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import red

def create_test_pdf(path):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 700, "Test PDF Page 1")
    c.showPage()
    c.drawString(100, 700, "Test PDF Page 2")
    c.save()

def create_test_signature():
    # Create a simple red square signature
    img_buffer = io.BytesIO()
    c = canvas.Canvas(img_buffer, pagesize=(100, 50))
    c.setFillColor(red)
    c.rect(0, 0, 100, 50, fill=1)
    c.save()
    img_buffer.seek(0)

    # Convert PDF to PNG? No, the service expects PNG.
    # Let's use PIL to make a PNG
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (100, 50), (255, 0, 0, 128)) # Semi-transparent red
    draw = ImageDraw.Draw(img)
    draw.line((0,0, 100,50), fill='black', width=3)
    draw.line((0,50, 100,0), fill='black', width=3)

    out_buffer = io.BytesIO()
    img.save(out_buffer, format='PNG')
    return "data:image/png;base64," + base64.b64encode(out_buffer.getvalue()).decode('utf-8')

def test_placements():
    print("Testing Signature Placements...")

    temp_dir = Path("simple_test_storage")
    temp_dir.mkdir(exist_ok=True)

    pdf_path = temp_dir / "original.pdf"
    create_test_pdf(pdf_path)

    sig_b64 = create_test_signature()

    # Mock OrderService (we only need the static method basically, but it's an instance method)
    # We can instantiate it with a mock DB session or None if not used in this method
    service = OrderService(db=None)

    # Test 1: New Sticker Syntax
    print("Test 1: New Sticker Syntax (Page 1 top-left, Page 2 bottom-right)")
    sig_data_new = {
        "signature_image": sig_b64,
        "placements": [
            {"page_number": 1, "x": 50, "y": 700, "width": 100, "height": 50},
            {"page_number": 2, "x": 400, "y": 100, "width": 150, "height": 75}
        ]
    }

    try:
        signed_path_new = service._apply_signature_to_pdf(str(pdf_path), sig_data_new)
        print(f"[SUCCESS] Signed PDF (New) created at: {signed_path_new}")
    except Exception as e:
        print(f"[ERROR] Test 1 failed: {e}")
        import traceback
        traceback.print_exc()

    # Test 2: Legacy Syntax
    print("\nTest 2: Legacy Syntax (Full Page Overlay on Page 1)")
    sig_data_legacy = {
        "signature_image": sig_b64,
        "page_number": 1,
        "position": {"x": 0, "y": 0} # Should be ignored/mapped to full page
    }

    try:
        signed_path_legacy = service._apply_signature_to_pdf(str(pdf_path), sig_data_legacy)
        print(f"[SUCCESS] Signed PDF (Legacy) created at: {signed_path_legacy}")
    except Exception as e:
        print(f"[ERROR] Test 2 failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_placements()
