from __future__ import annotations

from app.services.bigcommerce_analytics_cache import _classify_cpu_family


def test_intel_cpu_with_amd_radeon_graphics_classifies_as_intel():
    text = "Processor: Intel Core Ultra 7 265. Graphics: AMD Radeon Graphics."

    assert _classify_cpu_family(text) == "Intel"


def test_amd_radeon_graphics_alone_does_not_classify_as_amd_cpu():
    text = "Graphics: AMD Radeon 780M integrated graphics."

    assert _classify_cpu_family(text) is None


def test_amd_ryzen_processor_classifies_as_amd():
    text = "Processor: AMD Ryzen 5 PRO 8540U. Graphics: Radeon 740M."

    assert _classify_cpu_family(text) == "AMD"

