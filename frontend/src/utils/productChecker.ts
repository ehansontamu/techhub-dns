import type { ProductCheckerReport, ProductCheckerRow, ProductCheckerSection } from "../api/productChecker";

export const PRODUCT_CHECKER_SCOPE = "Only BigCommerce products marked Visible on Storefront are fetched. Hidden products are not checked, so a missing SKU match does not prove that the product is absent from BigCommerce.";

// Describe saved results at display time as well as new scans. Keep the API's
// category keys and comparison values intact; only the human-readable text changes.
function describeRow(section: ProductCheckerSection, row: ProductCheckerRow): string[] {
  switch (section) {
    case "missing_in_bigcommerce":
      return ["Active in inFlow; no matching SKU among visible BigCommerce products. The product may exist in BigCommerce with storefront visibility disabled."];
    case "missing_in_inflow":
      return ["Visible in BigCommerce; no matching SKU in the included inFlow records. Products in excluded inFlow categories are not checked."];
    case "discontinued_in_inflow":
      return ["Visible in a BigCommerce discontinued category (49–52); no matching SKU in the included inFlow records. This does not describe the product's inFlow status."];
    case "whitespace_skus":
      return [`${row.source ?? "The source"} SKU has leading or trailing whitespace. Matching ignores this surrounding whitespace.`];
    case "missing_custom_info":
      return [`First unfilled inFlow field found: ${row.field ?? "not recorded"}. Later fields are not listed for this SKU.`];
    case "closeout_y_and_bc_inventory_zero": {
      const active = row.inflow_active === true ? "active" : row.inflow_active === false ? "inactive" : "not recorded";
      return [
        `inFlow closeout flag (custom1): Y; inFlow status: ${active}.`,
        `BigCommerce inventory tracking: ${row.inventory_tracking ?? "not recorded"}; quantity used by this check: ${row.inventory_level ?? 0}.`,
      ];
    }
    case "bc_inconsistencies": {
      const issues: Record<string, string> = {
        "Missing page_title": "BigCommerce page title is empty",
        "Title whitespace mismatch": "BigCommerce product name and page title differ in leading or trailing whitespace",
        "Title case mismatch": "BigCommerce product name and page title differ in capitalization",
        "Title mismatch": "BigCommerce product name and page title differ",
      };
      return [`${issues[row.issue ?? ""] ?? "BigCommerce product name / page title difference"}. Product name: ${JSON.stringify(row.name)}; page title: ${JSON.stringify(row.page_title ?? "")}.`];
    }
    case "variants_missing_sku":
      return [`Variant ${row.variant_id ?? "(ID not recorded)"} of visible BigCommerce product ${row.product_id ?? "(ID not recorded)"} has a blank or whitespace-only SKU. It cannot be matched to inFlow by SKU.`];
    case "mismatched_fields":
      return row.details.map((detail) => detail
        .replace(/^BPN:/, "Commodity code (BPN):")
        .replace(/^Price:/, "Price used by this check:")
        .replace(/^Name whitespace:/, "Name whitespace differs:")
        .replace(/^Name:/, "Product names differ:"));
    case "wrong_BPN":
      return row.details.map((detail) => detail.replace(/^Expected /, "Commodity code required by this check: "));
    default:
      return row.details;
  }
}

export function describeProductCheckerReport(report: ProductCheckerReport): ProductCheckerReport {
  const reports = { ...report.reports };
  for (const section of Object.keys(reports) as ProductCheckerSection[]) {
    reports[section] = reports[section].map((row) => ({ ...row, details: describeRow(section, row) }));
  }
  return { ...report, reports };
}
