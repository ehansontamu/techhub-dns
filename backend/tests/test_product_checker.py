"""Offline regression coverage for V10 rules and the scan lifecycle."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.product_checker_comparison import (
    DESKTOP_CATEGORIES, EXCLUDED_CATEGORIES, LAPTOP_CATEGORIES,
    PRICING_SCHEME_ID, build_bigcommerce_index, compare_products,
)
from app.services.product_checker_service import ProductCheckerService, _release_lock
from app.utils.exceptions import DNSApiError, ExternalServiceError


def bc(**overrides):
    return {"id": 1, "name": "Laptop", "page_title": "Laptop", "sku": "SKU-1",
            "price": 100, "sale_price": 0, "categories": [],
            "bin_picking_number": "43211508", "inventory_tracking": "variant",
            "inventory_level": 0, **overrides}


def inflow(**overrides):
    return {"name": "Laptop", "sku": "SKU-1", "isActive": True,
            "productId": "8b2a80ff-6bbb-4653-80a0-781af7c9fb97",
            "categoryId": next(iter(LAPTOP_CATEGORIES)),
            "customFields": {"custom2": "43211508", **{f"custom{n}": "value" for n in range(3, 11)}},
            "prices": [{"pricingSchemeId": PRICING_SCHEME_ID, "unitPrice": 100}], **overrides}


class ComparisonTests(unittest.TestCase):
    def test_identical_catalogs_and_no_input_mutation(self):
        product = inflow()
        before = copy.deepcopy(product)
        result = compare_products([bc()], {}, [product])
        self.assertEqual(result["summary"]["total_findings"], 0)
        self.assertEqual(result["summary"]["matched_skus"], 1)
        self.assertEqual(product, before)

    def test_missing_buckets_exclusions_and_inactive(self):
        result = compare_products(
            [bc(sku="BC"), bc(id=2, sku="OLD", categories=[49])], {},
            [inflow(sku="IF"), inflow(sku="INACTIVE", isActive=False),
             inflow(sku="EXCLUDED", categoryId=next(iter(EXCLUDED_CATEGORIES)))],
        )
        reports = result["reports"]
        for key, sku in [("missing_in_bigcommerce", "IF"), ("missing_in_inflow", "BC"), ("discontinued_in_inflow", "OLD")]:
            self.assertEqual([row["sku"] for row in reports[key]], [sku])
        self.assertEqual(reports["wrong_BPN"], [])
        self.assertEqual(reports["mismatched_fields"], [])
        self.assertEqual(result["summary"]["inflow_excluded"], 1)

    def test_variant_price_precedence_and_product_fallback(self):
        for variant, expected in [
            ({"sale_price": 70, "price": 80}, "70"),
            ({"sale_price": 0, "price": 80}, "80"),
            ({"sale_price": None, "price": None}, "90"),
        ]:
            with self.subTest(variant=variant):
                index, _ = build_bigcommerce_index([bc(sale_price=90)], {1: [{"id": 4, "sku": "V", **variant}]})
                self.assertEqual(index["V"]["effective_price"], expected)
                self.assertNotIn("SKU-1", index)
        index, missing = build_bigcommerce_index([bc()], {1: [{"id": 4, "sku": " "}]})
        self.assertTrue(index["SKU-1"]["is_product_level"])
        self.assertEqual(missing[0]["variant_id"], 4)

    def test_variant_names_only_compare_whitespace(self):
        variants = {1: [{"id": 3, "sku": "SKU-1", "price": 100}]}
        report = compare_products([bc(name="Parent", page_title="Parent")], variants, [inflow()])
        self.assertEqual(report["reports"]["mismatched_fields"], [])
        report = compare_products([bc(name=" Laptop ")], variants, [inflow()])
        self.assertEqual(report["reports"]["mismatched_fields"][0]["mismatches"], ["Name whitespace mismatch"])
        report = compare_products([bc(name="Parent")], {}, [inflow()])
        self.assertIn("Name mismatch", report["reports"]["mismatched_fields"][0]["mismatches"])

    def test_price_bpn_and_first_missing_custom_field(self):
        product = inflow(customFields={"custom2": "wrong", "custom3": "present"})
        report = compare_products([bc(price=120)], {}, [product])["reports"]
        self.assertEqual(report["mismatched_fields"][0]["mismatches"], ["BPN mismatch", "Price mismatch"])
        self.assertEqual(report["missing_custom_info"][0]["field"], "custom4")
        self.assertEqual(len(report["wrong_BPN"]), 1)
        report = compare_products([bc(bin_picking_number="43211507")], {}, [inflow(categoryId=next(iter(DESKTOP_CATEGORIES)), customFields={"custom2": "43211507"})])["reports"]
        self.assertEqual(report["wrong_BPN"], [])

    def test_discontinued_exempts_fields_but_not_commodity_policy(self):
        report = compare_products([bc(categories=[52], price=1)], {}, [inflow(customFields={"custom2": "wrong"})])["reports"]
        self.assertEqual(report["mismatched_fields"], [])
        self.assertEqual(report["missing_custom_info"], [])
        self.assertEqual(len(report["wrong_BPN"]), 1)

    def test_closeout_includes_inactive_and_uses_configured_tracking_source(self):
        product = inflow(isActive=False, customFields={"custom1": " y "})
        cases = [
            ("variant", 0, 5, 1),    # Variant stock is zero even if other variants have stock.
            ("variant", 2, 0, 0),    # Product total must not override nonzero variant stock.
            ("variant", None, 0, 0), # Missing variant quantity is unknown, not zero.
            ("product", 0, 5, 0),    # Regression: unused base-variant zero caused false positives.
            ("product", 5, 0, 1),    # Regression: unused variant stock concealed product zero.
            ("product", 0, None, 0), # Missing product quantity must not fall back to the variant.
            ("none", 0, 0, 0),
            (None, 0, 0, 0),
        ]
        for tracking, variant_qty, product_qty, expected in cases:
            with self.subTest(tracking=tracking, variant=variant_qty, product=product_qty):
                report = compare_products([bc(inventory_tracking=tracking, inventory_level=product_qty)],
                                          {1: [{"sku": "SKU-1", "inventory_level": variant_qty}]}, [product])["reports"]
                self.assertEqual(len(report["closeout_y_and_bc_inventory_zero"]), expected)
                self.assertEqual(report["mismatched_fields"], [])
                self.assertEqual(report["wrong_BPN"], [])
                if expected:
                    row = report["closeout_y_and_bc_inventory_zero"][0]
                    self.assertEqual(row["inventory_source"], tracking)
                    self.assertEqual(row["product_inventory_level"], product_qty)
                    self.assertEqual(row["variant_inventory_level"], variant_qty)

    def test_variant_tracking_requires_a_variant_quantity_and_invalid_is_not_zero(self):
        product = inflow(customFields={"custom1": "Y"})
        result = compare_products([bc(inventory_tracking="variant", inventory_level=0)], {}, [product])
        self.assertEqual(result["reports"]["closeout_y_and_bc_inventory_zero"], [])
        for quantity in (None, "", "invalid", 0.5, False, float("nan"), float("inf")):
            with self.subTest(quantity=quantity):
                result = compare_products([bc(inventory_tracking="product", inventory_level=quantity)], {}, [product])
                self.assertEqual(result["reports"]["closeout_y_and_bc_inventory_zero"], [])

    def test_discontinued_category_does_not_exempt_a_visible_zero_stock_closeout(self):
        result = compare_products([bc(inventory_tracking="product", categories=[49])], {},
                                  [inflow(customFields={"custom1": "Y"})])
        row = result["reports"]["closeout_y_and_bc_inventory_zero"][0]
        self.assertTrue(row["bigcommerce_discontinued"])
        self.assertTrue(row["bigcommerce_is_visible"])
        self.assertEqual(row["inventory_source"], "product")
        self.assertEqual(result["inventory_check_version"], 2)

    def test_sku_whitespace_trimmed_but_case_sensitive(self):
        report = compare_products([bc(sku=" SKU-1 ")], {}, [inflow(sku="SKU-1 ")])["reports"]
        self.assertEqual(len(report["whitespace_skus"]), 2)
        self.assertEqual(report["whitespace_skus"][0]["sku"], "SKU-1 ")
        self.assertEqual(report["missing_in_bigcommerce"], [])
        report = compare_products([bc(sku="sku-1")], {}, [inflow()])["reports"]
        self.assertEqual(len(report["missing_in_bigcommerce"]), 1)

    def test_title_checks_once_per_product(self):
        for title, issue in [("", "Missing page_title"), ("Laptop ", "Title whitespace mismatch"), ("LAPTOP", "Title case mismatch"), ("Other", "Title mismatch")]:
            with self.subTest(title=title):
                result = compare_products([bc(page_title=title)], {1: [{"sku": "A"}, {"sku": "B"}]}, [])
                self.assertEqual(len(result["reports"]["bc_inconsistencies"]), 1)
                self.assertEqual(result["reports"]["bc_inconsistencies"][0]["issue"], issue)

    def test_links_use_parent_product_id_and_matched_inflow_id(self):
        variants = {583: [{"id": 9999, "sku": " SKU-1 ", "price": 120}]}
        result = compare_products([bc(id=583)], variants, [inflow()])
        for category in ("mismatched_fields", "whitespace_skus"):
            row = result["reports"][category][0]
            self.assertEqual(row["bigcommerce_product_id"], 583)
            self.assertTrue(row["bigcommerce_is_visible"])
            self.assertEqual(row["inflow_product_id"], inflow()["productId"])
        self.assertEqual(result["link_metadata_version"], 1)

    def test_hidden_and_excluded_link_records_do_not_change_findings(self):
        visible = bc(id=1, sku="EXCLUDED")
        hidden = bc(id=583, sku="SKU-1", is_visible=False, page_title="Different")
        excluded = inflow(sku="EXCLUDED", categoryId=next(iter(EXCLUDED_CATEGORIES)))
        source = [inflow(), excluded]
        baseline = compare_products([visible], {}, source)
        enriched = compare_products([visible], {}, source, bigcommerce_link_products=[visible, hidden])
        self.assertEqual(baseline["summary"], enriched["summary"])
        self.assertEqual({key: len(rows) for key, rows in baseline["reports"].items()},
                         {key: len(rows) for key, rows in enriched["reports"].items()})
        row = enriched["reports"]["missing_in_bigcommerce"][0]
        self.assertEqual(row["bigcommerce_product_id"], 583)
        self.assertFalse(row["bigcommerce_is_visible"])
        self.assertEqual(row["inflow_product_id"], inflow()["productId"])
        row = enriched["reports"]["missing_in_inflow"][0]
        self.assertEqual(row["inflow_product_id"], excluded["productId"])
        self.assertTrue(row["inflow_link_excluded"])
        self.assertEqual(enriched["reports"]["bc_inconsistencies"], [])

    def test_links_handle_missing_skus_and_preserve_compared_duplicate(self):
        visible = bc(id=1, price=120)
        hidden = bc(id=583, is_visible=False)
        excluded = inflow(productId="excluded-id", categoryId=next(iter(EXCLUDED_CATEGORIES)))
        variants = {1: [{"id": 22, "sku": ""}]}
        result = compare_products([visible], variants, [inflow(), excluded], bigcommerce_link_products=[visible, hidden])
        missing = result["reports"]["variants_missing_sku"][0]
        self.assertEqual(missing["bigcommerce_product_id"], 1)
        self.assertIsNone(missing["inflow_product_id"])
        compared = result["reports"]["mismatched_fields"][0]
        self.assertEqual(compared["bigcommerce_product_id"], 1)
        self.assertEqual(compared["inflow_product_id"], inflow()["productId"])
        self.assertFalse(compared["inflow_link_excluded"])
        absent = compare_products([], {}, [inflow()])["reports"]["missing_in_bigcommerce"][0]
        self.assertIsNone(absent["bigcommerce_product_id"])


class ScanLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = SimpleNamespace(
            inflow_api_key="test-secret", inflow_company_id="test-company",
            inventory_reorder_bigcommerce_token="test-token",
            inventory_reorder_bigcommerce_store_id="test-store",
            inventory_reorder_bigcommerce_base_url="https://example.test/stores",
            inventory_reorder_request_delay_seconds=0,
        )
        self.service = ProductCheckerService(self.settings, Path(self.temporary.name))

    def test_config_contains_no_secrets_and_missing_config_cannot_start(self):
        self.assertEqual(self.service.config_status(), {"configured": True, "missing": []})
        self.settings.inflow_api_key = None
        with self.assertRaises(DNSApiError) as error:
            self.service.start("admin")
        self.assertEqual(error.exception.status_code, 503)

    def test_shared_lock_prevents_duplicate_scan_and_status_survives_new_instance(self):
        with patch("app.services.product_checker_service.threading.Thread") as worker:
            payload, created = self.service.start("admin")
            self.assertTrue(created)
            job, handle = worker.call_args.kwargs["args"]
        try:
            other = ProductCheckerService(self.settings, Path(self.temporary.name))
            repeated, created = other.start("other admin")
            self.assertFalse(created)
            self.assertEqual(repeated["job"]["id"], payload["job"]["id"])
            with patch.object(self.service, "_scan", return_value=compare_products([], {}, [])):
                self.service._run(job, handle)
            handle = None
            self.assertEqual(other.status(include_report=True)["report"]["job_id"], job["id"])
            self.assertEqual(other.status(include_report=True)["report"]["bigcommerce_store_id"], "test-store")
            self.assertEqual(other.status()["job"]["status"], "completed")
        finally:
            if handle:
                _release_lock(handle)

    def test_failed_scan_preserves_successful_report_and_sanitizes_errors(self):
        self.service._write("report.json", {"job_id": "previous"})
        with patch("app.services.product_checker_service.threading.Thread") as worker:
            self.service.start("admin")
            job, handle = worker.call_args.kwargs["args"]
        with patch.object(self.service, "_scan", side_effect=RuntimeError("test-secret")):
            self.service._run(job, handle)
        status = self.service.status(include_report=True)
        self.assertEqual(status["report"]["job_id"], "previous")
        self.assertEqual(status["job"]["status"], "failed")
        self.assertNotIn("test-secret", status["job"]["error"])

    def test_interrupted_worker_is_recoverable(self):
        with patch("app.services.product_checker_service.threading.Thread") as worker:
            self.service.start("admin")
            _, handle = worker.call_args.kwargs["args"]
        _release_lock(handle)
        self.assertEqual(self.service.status()["job"]["status"], "failed")

    def test_variant_pagination_and_invalid_response_fail_closed(self):
        with patch.object(self.service, "_get", side_effect=[{"data": [{"id": n} for n in range(250)]}, {"data": [{"id": 251}]}]) as get:
            rows = self.service._bigcommerce_pages(None, "products/1/variants", {}, lambda *_: None, 10)
            self.assertEqual(len(rows), 251)
            self.assertEqual([call.args[3]["page"] for call in get.call_args_list], [1, 2])
        with patch.object(self.service, "_get", return_value={"error": "upstream"}):
            with self.assertRaises(ExternalServiceError):
                self.service._bigcommerce_pages(None, "products", {}, lambda *_: None, 10)


if __name__ == "__main__":
    unittest.main()
