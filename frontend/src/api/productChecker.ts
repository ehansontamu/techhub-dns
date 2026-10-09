import { apiClient } from "./client";

export const PRODUCT_CHECKER_SECTIONS = [
  {
    key: "missing_in_bigcommerce",
    label: "No visible BigCommerce match",
    description: "Active inFlow SKUs with no match among visible BigCommerce products. A product may already exist in BigCommerce with storefront visibility disabled.",
    notes: [
      "Hidden BigCommerce products are looked up for record links but are excluded from the comparison. A link labeled hidden confirms a hidden record with that SKU was found. A missing link does not prove the product is absent; it may use a different SKU.",
      "Only active inFlow products outside Internal, Category Needed, and Testing are listed here. Matching ignores surrounding SKU whitespace but requires the same letter case.",
    ],
  },
  {
    key: "missing_in_inflow",
    label: "No inFlow SKU match",
    description: "Visible BigCommerce SKUs with no match in the inFlow products included in this scan. BigCommerce discontinued categories are listed separately.",
    notes: [
      "Both active and inactive inFlow products are searched. Internal, Category Needed, and Testing are excluded, so a product in one of those categories can still exist in inFlow.",
      "Matching uses SKUs, not product names. A different SKU or letter case will not match.",
    ],
  },
  {
    key: "mismatched_fields",
    label: "Field differences",
    description: "Differences in the price, commodity code, or name values compared for matching SKUs. Only active inFlow products are checked; BigCommerce discontinued categories are exempt.",
    notes: [
      "BigCommerce price uses the first available nonzero value in this order: variant sale price, variant price, product sale price, product price. If none is selected, the comparison uses zero.",
      "inFlow price comes from one designated pricing scheme; other pricing schemes are not compared. A missing price is compared as zero. The listed amounts may differ from a customer's final checkout price.",
      "Commodity code compares inFlow custom2 with BigCommerce's Bin Picking Number (BPN). This category checks whether they agree; the commodity-code rule category checks the expected desktop or laptop code.",
      "Full product-name comparisons run only when the product SKU is used because no variant has a usable SKU. For variant SKUs, only whitespace differences between the parent product name and the inFlow name are reported. Whitespace is collapsed for this name comparison; letter case is preserved.",
    ],
  },
  {
    key: "wrong_BPN",
    label: "Commodity code rule checks",
    description: "Matching SKUs where either system's commodity code differs from the expected code for the inFlow desktop or laptop categories recognized by this checker.",
    notes: [
      "The required code is 43211507 for desktops and 43211508 for laptops. The checker reads inFlow custom2 and BigCommerce's Bin Picking Number (BPN).",
      "Only active inFlow products with a visible BigCommerce SKU match are checked. BigCommerce discontinued categories are included here. Other inFlow categories are not checked against these codes.",
    ],
  },
  {
    key: "whitespace_skus",
    label: "SKU edge whitespace",
    description: "SKUs with leading or trailing whitespace in the records used for matching. This whitespace is ignored when matching the two systems.",
    notes: [
      "Includes visible BigCommerce records and included inFlow records, whether active or inactive. Whitespace inside a SKU is not checked or removed; letter case still matters.",
      "A SKU can appear once for each source. The quotes around displayed SKUs are added by the report to make whitespace easier to see.",
    ],
  },
  {
    key: "discontinued_in_inflow",
    label: "Discontinued: no inFlow match",
    description: "Visible BigCommerce SKUs in discontinued categories 49–52 with no match in the included inFlow products. “Discontinued” describes the BigCommerce category, not the inFlow status.",
    notes: [
      "Both active and inactive inFlow products are searched, after excluding Internal, Category Needed, and Testing. An inFlow product in an excluded category or with a different SKU will not match.",
      "A discontinued category and storefront visibility are separate settings: these BigCommerce products are still marked visible.",
    ],
  },
  {
    key: "missing_custom_info",
    label: "First missing custom field",
    description: "The first inFlow field flagged as missing, from custom3 through custom10, for each matching SKU in the desktop or laptop categories recognized by this checker.",
    notes: [
      "Only active inFlow products with a visible BigCommerce SKU match are checked. BigCommerce discontinued categories are exempt.",
      "The check stops at the first unfilled field for each SKU. Later fields may also need information. Field identifiers are shown because the checker does not load their display names from inFlow.",
      "Absent, empty, false, or numeric-zero values are flagged. Whitespace-only text is not flagged, and entered values are not checked for correctness.",
    ],
  },
  {
    key: "closeout_y_and_bc_inventory_zero",
    label: "Closeouts with zero BC stock",
    description: "Visible BigCommerce SKUs marked closeout in inFlow with zero stock in the inventory source selected by BigCommerce's tracking setting. Includes active and inactive inFlow products.",
    notes: [
      "The inFlow closeout flag is custom1 = Y, ignoring surrounding whitespace and letter case. The BigCommerce parent product must be visible and have product or variant inventory tracking enabled.",
      "With product-level tracking, the check uses the parent product quantity. With variant-level tracking, it uses that SKU's variant quantity. It never substitutes a quantity from the other tracking mode; missing or invalid quantities are not treated as zero.",
      "Products with inventory tracking disabled are skipped. This checks recorded stock, not inFlow stock or whether a customer can place an order.",
      "BigCommerce discontinued categories are included. Category membership, Visible on Storefront, and inventory tracking are separate settings. A row means the product was marked visible when scanned; it does not mean every purchasing option is enabled.",
    ],
  },
  {
    key: "bc_inconsistencies",
    label: "Product name / page title",
    description: "Visible BigCommerce products whose page title is empty or differs from their product name. This compares two BigCommerce fields, not BigCommerce with inFlow.",
    notes: [
      "Only products represented in the SKU comparison are checked. No inFlow match is required, and discontinued categories are included.",
      "At most one row is shown per product, even when it has multiple variant SKUs. The displayed SKU is one representative SKU. Capitalization and whitespace differences can produce a row; a different page title may be intentional.",
    ],
  },
  {
    key: "variants_missing_sku",
    label: "Variants without usable SKUs",
    description: "Variants of visible BigCommerce products with a blank or whitespace-only SKU. These variants cannot be matched to inFlow by SKU.",
    notes: [
      "One row is shown per variant. If no variant has a usable SKU, the checker falls back to the product SKU when it is populated.",
      "This category checks variant SKUs only. It is not a complete list of products without SKUs in either system.",
    ],
  },
] as const;

export type ProductCheckerSection = typeof PRODUCT_CHECKER_SECTIONS[number]["key"];

export interface ProductCheckerRow {
  name: string;
  sku: string;
  details: string[];
  source?: string;
  mismatches?: string[];
  field?: string;
  inflow_active?: boolean;
  inventory_tracking?: string;
  inventory_level?: number;
  inventory_source?: "product" | "variant";
  product_inventory_level?: number | null;
  variant_inventory_level?: number | null;
  bigcommerce_discontinued?: boolean;
  page_title?: string;
  issue?: string;
  product_id?: number;
  variant_id?: number;
  bigcommerce_product_id?: number | null;
  bigcommerce_is_visible?: boolean | null;
  inflow_product_id?: string | null;
  inflow_link_excluded?: boolean;
}

export interface ProductCheckerReport {
  job_id: string;
  completed_at: string;
  started_by: string;
  link_metadata_version?: number;
  inventory_check_version?: number;
  bigcommerce_store_id?: string;
  summary: {
    bigcommerce_products: number;
    bigcommerce_skus: number;
    inflow_products: number;
    inflow_eligible: number;
    inflow_excluded: number;
    inflow_inactive: number;
    matched_skus: number;
    total_findings: number;
  };
  reports: Record<ProductCheckerSection, ProductCheckerRow[]>;
}

export interface ProductCheckerStatus {
  config: { configured: boolean; missing: string[] };
  job: {
    id: string;
    status: "running" | "completed" | "failed";
    started_at: string;
    finished_at: string | null;
    started_by: string;
    progress: number;
    message: string;
    error: string | null;
  } | null;
}

export interface ProductCheckerResponse extends ProductCheckerStatus {
  report: ProductCheckerReport | null;
}

export const productCheckerApi = {
  async getData(): Promise<ProductCheckerResponse> {
    return (await apiClient.get<ProductCheckerResponse>("/system/product-checker")).data;
  },
  async getStatus(): Promise<ProductCheckerStatus> {
    return (await apiClient.get<ProductCheckerStatus>("/system/product-checker/status")).data;
  },
  async refresh(): Promise<ProductCheckerStatus> {
    return (await apiClient.post<ProductCheckerStatus>("/system/product-checker/refresh")).data;
  },
};
