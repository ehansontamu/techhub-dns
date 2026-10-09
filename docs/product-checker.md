# Product Checker

The admin-only applet at `/product-checker` compares the visible BigCommerce catalog
with inFlow. It ports the reporting rules from `BC_inFlow_checker_V10.py` into a
read-only service. The sidebar link, page, and every API endpoint enforce admin
access. Scans are started manually; opening the page only reads saved results.

The checker reuses `inflow_api_key`, `inflow_company_id`, `inflow_api_url`, and the
existing `inventory_reorder_bigcommerce_*` settings, including their environment
aliases (`BC_ACCESS_TOKEN`, `BC_STORE_HASH`, etc.). It does not need another login,
Azure Key Vault client, or inventory location. The existing API token must have
BigCommerce catalog read access. Credentials stay on the server.

## Comparison rules

- Fetch BigCommerce products and every page of their variants; only visible
  products enter the comparison. Hidden records supply product links only. Use trimmed,
  case-sensitive variant SKUs, falling back to the product SKU when no variant has
  a SKU. Report variants without SKUs separately.
- Include active and inactive inFlow products with prices. Exclude the source
  script's Internal, Category Needed, and Testing category IDs.
- “No visible BigCommerce match” applies only to active inFlow SKUs. It does not
  prove a product is absent from BigCommerce: hidden products are not compared.
  BigCommerce SKUs without an included inFlow match are split by BigCommerce
  discontinued categories 49–52; those labels do not describe inFlow status.
- Compare the source pricing scheme, BPN (`custom2`), and names. Variant sale price,
  variant price, product sale price, and product price are considered in that order,
  using the source script's nonzero fallback rule. Full name comparisons apply only
  to product-level records; whitespace differences also apply to variant records.
- Desktops require commodity code 43211507; laptops require 43211508. Report the
  first missing field in `custom3`–`custom10`. Discontinued items are exempt from
  field comparisons and custom-info checks, but retain the commodity-code check.
- Closeouts have `custom1=Y`, storefront visibility enabled, and tracked BigCommerce
  inventory of zero. Include inactive inFlow items and BigCommerce discontinued
  categories. Use parent product inventory for product tracking, and the matched
  variant's inventory for variant tracking; never fall back between these sources.
  Ignore untracked inventory and missing/invalid quantities. This corrects the V10
  variant-first fallback, which could report zero from an unused inventory field.
  Rows show the selected source and both returned quantities. Discontinued category
  membership does not itself disable storefront visibility or purchasing.
- Compare BigCommerce names with page titles once per indexed product.

The nine original report categories and the missing-variant-SKU diagnostic are
searchable and paginated. Each category explains its scope, exemptions, and fallback
rules. Counts are report rows: one SKU can appear in multiple categories and one row
can contain multiple differences. SKU matches include inactive inFlow products and
do not indicate that the compared fields agree. The inFlow product count includes
blank-SKU records even though those records cannot be matched.

JSON and text downloads contain the entire displayed report, independent of the
current search, along with the scope and explanations. JSON keeps the existing
category keys and adds a `report_guide`. The UI formats both new and saved report
details with the current wording, without changing stored data or comparison rules.

## Product record links

Each report row retains the BigCommerce parent product `id` and the inFlow
`productId` when known. BigCommerce links use the scan's configured store hash:
`https://store-{store}.mybigcommerce.com/manage/products/edit/{id}`.
inFlow links use `https://app.inflowinventory.com/products/{productId}`.
Variant rows open the parent BigCommerce product, not a variant ID.

Links appear beneath the product name and open in a new tab. Hidden BigCommerce
products and excluded inFlow categories can supply links without affecting report
membership or counts; these links are labeled accordingly. Lookup prefers the
record actually used in the comparison and uses the same trimmed, case-sensitive
SKU. Product SKUs can additionally supply links when comparison uses variant SKUs.
No link is invented when a matching record ID is unavailable.

Run a new scan after deploying this change: old scans did not retain the IDs needed
for links. JSON and text exports include the available record URLs. No database
migration or new connection settings are required.

Reports with `inventory_check_version: 2` use the tracking-aware closeout rule.
Older saved scans display a rerun notice and do not present their quantity as
verified by the updated check. Run a new scan after deploying this correction.

## Scan lifecycle

`GET /api/system/product-checker` returns configuration status, the latest job, and
the last successful report. `POST /api/system/product-checker/refresh` starts a
background scan or returns the current running scan. The page polls
`GET /api/system/product-checker/status` for progress.

Status and reports are written atomically beneath `backend/storage/product_checker`
(ignored by git). An operating-system file lock prevents overlapping scans across
web workers sharing that directory. A worker exit releases the lock; the next
status read marks an interrupted job failed so an admin can retry. Failed or partial
fetches never replace the last successful report. Upstream GETs use timeouts and
bounded retries; no product writes, notifications, or external sync actions occur.

All web workers must share the storage directory, as with other local app artifacts.
No database models or migrations are needed. The applet does not schedule scans.

## Offline checks

From `backend/`: `python -m unittest tests.test_product_checker -v` covers comparison
rules, pagination, locking, interruption, configuration, and report preservation.
With backend dependencies and pytest installed, also run
`python -m pytest tests/test_product_checker_routes.py -q` for route authorization.
From `frontend/`: `npm run test -- src/pages/ProductChecker.test.tsx`,
`npm run lint`, and `npx tsc --noEmit`.
