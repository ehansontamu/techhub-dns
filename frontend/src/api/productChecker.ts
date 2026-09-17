import { apiClient } from "./client";

export const PRODUCT_CHECKER_SECTIONS = [
  { key: "missing_in_bigcommerce", label: "Missing in BigCommerce", description: "Active inFlow products absent from the visible BigCommerce catalog." },
  { key: "missing_in_inflow", label: "Missing in inFlow", description: "Visible BigCommerce products absent from eligible inFlow products, excluding discontinued categories." },
  { key: "mismatched_fields", label: "Field mismatches", description: "Price, commodity code, and name differences. Full name comparisons apply to product-level SKUs; variant names are checked for whitespace only." },
  { key: "wrong_BPN", label: "Wrong commodity code", description: "Desktops require BPN 43211507; laptops require BPN 43211508 in both systems." },
  { key: "whitespace_skus", label: "SKU whitespace", description: "Leading or trailing whitespace in either catalog. Matching uses trimmed, case-sensitive SKUs." },
  { key: "discontinued_in_inflow", label: "Discontinued / missing", description: "BigCommerce products in discontinued categories 49–52 that are absent from eligible inFlow products." },
  { key: "missing_custom_info", label: "Missing custom info", description: "The first missing field from custom3–custom10 for each active desktop or laptop. Discontinued BigCommerce products are exempt." },
  { key: "closeout_y_and_bc_inventory_zero", label: "Closeouts at zero", description: "inFlow custom1 is Y and tracked BigCommerce inventory is zero. Includes inactive inFlow products." },
  { key: "bc_inconsistencies", label: "Page title issues", description: "BigCommerce product names and page titles differ, or the page title is missing. One finding per product." },
  { key: "variants_missing_sku", label: "Variants without SKUs", description: "BigCommerce variants without a SKU cannot be matched to inFlow. The product SKU is used when no variant has a SKU." },
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
  page_title?: string;
  issue?: string;
  product_id?: number;
  variant_id?: number;
}

export interface ProductCheckerReport {
  job_id: string;
  completed_at: string;
  started_by: string;
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
