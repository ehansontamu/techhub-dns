import type { ProductCheckerReport, ProductCheckerRow, ProductCheckerSection } from "../api/productChecker";

export const PRODUCT_CHECKER_SCOPE = "Only BigCommerce products marked Visible on Storefront are compared. Hidden products are not checked, but they may supply record links. A missing comparison match does not prove that the product is absent from BigCommerce.";

export function getProductCheckerLinks(report: ProductCheckerReport, row: ProductCheckerRow) {
  const store = report.bigcommerce_store_id;
  const productId = row.bigcommerce_product_id;
  const inflowId = row.inflow_product_id?.trim();
  return {
    bigcommerce: store && /^[a-z0-9]+$/i.test(store) && productId != null && Number.isSafeInteger(productId) && productId > 0
      ? `https://store-${store}.mybigcommerce.com/manage/products/edit/${productId}` : null,
    inflow: inflowId ? `https://app.inflowinventory.com/products/${encodeURIComponent(inflowId)}` : null,
  };
}

// Describe saved results at display time as well as new scans. Keep the API's
// category keys and comparison values intact; only the human-readable text changes.
function describeRow(section: ProductCheckerSection, row: ProductCheckerRow): string[] {
  switch (section) {
    case "missing_in_bigcommerce":
      if (row.bigcommerce_product_id && row.bigcommerce_is_visible === false) {
        return ["Active in inFlow; a matching SKU was found in a hidden BigCommerce product. It is excluded from the visible-product comparison."];
      }
      if (row.bigcommerce_product_id && row.bigcommerce_is_visible === true) {
        return ["Active in inFlow; a visible BigCommerce product was found by its product SKU, but that SKU is not used in the variant-based comparison."];
      }
      return ["Active in inFlow; no matching SKU among visible BigCommerce products. The product may exist in BigCommerce with storefront visibility disabled."];
    case "missing_in_inflow":
      if (row.inflow_product_id && row.inflow_link_excluded) {
        return ["Visible in BigCommerce; a matching inFlow product was found in an excluded category. Its link is available, but the product is not included in the comparison."];
      }
      return ["Visible in BigCommerce; no matching SKU in the included inFlow records. Products in excluded inFlow categories are not checked."];
    case "discontinued_in_inflow":
      return ["Visible in a BigCommerce discontinued category (49–52); no matching SKU in the included inFlow records. This does not describe the product's inFlow status."];
    case "whitespace_skus":
      return [`${row.source ?? "The source"} SKU has leading or trailing whitespace. Matching ignores this surrounding whitespace.`];
    case "missing_custom_info":
      return [`First unfilled inFlow field found: ${row.field ?? "not recorded"}. Later fields are not listed for this SKU.`];
    case "closeout_y_and_bc_inventory_zero": {
      const active = row.inflow_active === true ? "active" : row.inflow_active === false ? "inactive" : "not recorded";
      const details = [
        `inFlow closeout flag (custom1): Y; inFlow status: ${active}.`,
        "BigCommerce: marked Visible on Storefront at the time of this scan. This does not confirm that purchasing is enabled.",
      ];
      if (!row.inventory_source) {
        return [...details, `Earlier scan: tracking ${row.inventory_tracking ?? "not recorded"}; previously reported quantity ${row.inventory_level ?? "not recorded"}. Run a new check to verify stock using the correct tracking source.`];
      }
      details.push(`BigCommerce inventory tracking: ${row.inventory_tracking}; ${row.inventory_source === "product" ? "parent product" : "SKU variant"} quantity checked: ${row.inventory_level ?? "not recorded"}.`);
      details.push(`API quantities — parent product: ${row.product_inventory_level ?? "not returned"}; SKU variant: ${row.variant_inventory_level ?? "not returned"}. Only the configured tracking source is used.`);
      if (row.bigcommerce_discontinued) {
        details.push("In a BigCommerce discontinued category (49–52). That category does not turn off storefront visibility or inventory tracking.");
      }
      return details;
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
