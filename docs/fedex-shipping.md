# FedEx shipping pilot

This change adds a packing workspace under **Shipping** after orders complete shipping QA.
Staff choose a city, then a named saved destination, and select orders to pack together. It uses **FedEx sandbox only**.
Sandbox tracking numbers and labels must not be used for real deliveries.

## Staff workflow

1. An admin opens **Shipping → Product shipping weights**. Products from locally
   synced orders appear automatically; **Import full Inflow catalog** adds active
   catalog entries. Importing preserves saved weights. Unknown weights stay blank.
2. Save each product's weight **per unit in pounds**, including manufacturer
   packaging that remains on the item. The catalog is keyed by Inflow product ID,
   so different products with the same name are independent.
3. In **Shipping**, choose the destination **City**. If the city has one waiting
   saved location, it opens automatically (for example, Galveston). If there are
   several, choose the **Location**, such as **Dallas 3302** or **Dallas 3300**.
   Locations are matched from order delivery-address fields and FedEx contact names.
   Select the orders to combine, then click **Pack N orders together**. This is the
   only selection method; order numbers identify the displayed rows.
   Assign each unit to
   Box 1, Box 2, etc., or **Use as box** for one product in its own shipping carton.
   Two monitors shipped in their own cartons require two packages, not one package
   containing two monitors. Partial orders use only that leg's unshipped picks;
   service/imaging lines are excluded. Up to 50 orders for the same saved location
   can share boxes and one FedEx shipment. Identical products on different orders
   retain their own order IDs and quantities. Orders at different locations cannot
   be combined, even when an admin uses a manual recipient override.
   Ambiguous or unmatched destinations appear under **Address needs review**;
   review the source order/shared book or prepare an individual shipment. The app
   never combines all orders merely because their city names match. Existing
   shipment members are reserved and reopen the same shipment from any member.
4. Review package weights. Each package uses only `saved product weight × quantity`
   summed across its assigned items. No packing allowances, dimensions, or manual
   package-weight override are used. The calculated weight must be more than zero
   and at most 150 lb per package, with up to 40 packages per shipment.
5. An admin imports the FedEx CSV once under **Shipping → Manage address book**.
   Contacts persist in the database across shipments and server restarts. The
   shipment screen has no upload field: staff search and choose saved contacts.
   Normal users cannot edit the recipient, phone, address, or residential flag.
   The backend checks the chosen contact exists and all recipient fields match
   before saving packing, requesting prices, or creating the shipment. If a saved
   contact changes, reselect it and refresh the packing and quote.
   Admins can enable **One-off address override (admin only)** for exceptional
   destinations. This permits manual fields for that shipment without adding or
   editing the shared book; the override and actor are audited. An admin must
   complete that shipment. Ordinary users cannot enable this through the API.
   The address book is an imported copy, not a live FedEx synchronization.
6. Select **Save packing & review totals**, then **Get FedEx prices**. Only the cheapest eligible whole-shipment USD account estimate is shown.
   The backend selects it automatically; operators cannot choose a higher-priced
   service. Previous multi-service quotes must be refreshed before shipping. Supported domestic parcel services include Ground/Home Delivery,
   Express Saver, 2Day/2Day A.M., and Standard/Priority/First Overnight when eligible.
   Specialized services such as freight and Ground Economy are not offered here.
   No hard-coded prices are used. Weight-only estimates may change with dimensional
   billing or other carrier adjustments. Quotes expire after 15 minutes or a
   ship-date change. Saving any packing or recipient changes clears old quotes.
7. Review the service and price, then **Create FedEx sandbox shipment & print labels**.
   If the cheapest whole-shipment estimate is **strictly greater than $100 USD**, the
   dialog says **Hey, are you sure?**, shows the amount, and requires **Yes, create
   shipment**. $100.00 does not trigger the high-cost warning; $100.01 does. The
   backend independently requires explicit high-cost confirmation tied to the
   current draft revision and valid quote. Cancelling creates nothing. This creates
   one multi-package shipment and queues each package's 4 × 6 PDF label.
8. Inspect tracking numbers and print status. Download or reprint the **saved** label
   when necessary. Reprinting does not create another shipment. A completed print
   job means SumatraPDF returned success; physically check the label and barcode.

Creation and printing leave the existing order's Shipping/Work Area status intact.
They do not mark the order Delivered, write sandbox tracking into real carrier
fields, move it to the dock, fulfill Inflow, notify a customer, or book a pickup.
The existing manual carrier-handoff action remains available for normal operations.
The sandbox request specifies an existing scheduled pickup; it does not create one.

## Product names, SKUs, and saved weights

The product list combines identities from locally synced order lines with any
products imported from the active Inflow catalog. Products and unit weights are
keyed by the Inflow product ID: the same product shares one weight across orders,
while two different product IDs stay separate even if they have the same SKU.

Product names use catalog/embedded product identity before short plain-text order
labels. HTML specifications and long description bodies are never used as titles.
When no usable name is available, the SKU (or product ID) appears until an admin
uses **Import full Inflow catalog** to refresh product names. Importing preserves
saved unit weights. The app does not extract or guess shipping weights from a
specification's advertised product weight.

Previously stored HTML names are resolved safely on read without deleting products
or weights. No data migration is required for this naming correction. Names are
shown as ordinary React text; the app does not render description HTML.

## Server setup

Apply the additive migration using the existing deployment process:

```sh
cd backend
alembic upgrade head
```

Configure the following in the backend environment, then restart the web app:

```dotenv
FEDEX_ENABLED=true
FEDEX_CLIENT_ID=<FedEx sandbox API key>
FEDEX_CLIENT_SECRET=<FedEx sandbox secret>
FEDEX_ACCOUNT_NUMBER=<FedEx test shipping account number>
FEDEX_SHIPPER_JSON={"name":"TechHub","company":"Texas A&M University","phone":"YOUR_PHONE","address1":"YOUR_STREET","address2":"","city":"College Station","state":"TX","postal_code":"77843","country":"US","residential":false}
```

Use credentials from a FedEx Developer Portal sandbox project with Ship API and
Rates and Transit Times API access.
Do not put credentials into source control, frontend variables, or chat. The hostname
is fixed to `https://apis-sandbox.fedex.com`; browser requests cannot choose another
account, sender, or endpoint. Production activation is deliberately outside this pilot.
Without configuration, staff can still maintain weights and save packing plans.

## Brother QL-1110NWB setup

Reuse the **existing Windows print-agent workstation and backend queue**. Install
the Brother Windows driver and add the QL-1110NWB as a Windows printer (USB or
network, as appropriate). Verify it can print from that workstation.

Update the agent files and add its exact Windows queue name to `ops/print_agent/.env`:

```dotenv
PRINTER_NAME=<existing picklist printer queue>
LABEL_PRINTER_NAME=Brother QL-1110NWB
```

Keep the existing `API_BASE_URL`, `AGENT_TOKEN`, and `SUMATRA_PDF_PATH`. Restart
the agent. Use label stock wide enough for the full 4 × 6 label, and configure the
Brother driver for the installed stock and correct cutting settings. The agent
prints PDFs at actual size (`noscale`). Confirm the complete label fits and its
barcode scans. Media sizing, margins, orientation, and the physical printer need
workstation verification; they cannot be verified from the cloud development machine.

When `LABEL_PRINTER_NAME` is empty, the agent claims only picklists. Legacy agents
also claim only picklists. With it configured, the upgraded agent claims both job
types and routes each to its corresponding printer. The bearer-token API and
polling fallback are reused. No browser print dialog or public printer connection
is needed. Label creation queues jobs durably; the agent's regular poll discovers
them even without a Socket.IO wake-up.

## Failure and recovery behavior

- Packing uses optimistic revisions. Stale edits, item changes, product-weight changes, or saved-contact
  changes require saving/reviewing again before shipment creation.
- Creation is claimed and committed before contacting FedEx. Concurrent staff
  requests and repeated browser requests cannot submit the same order twice.
- A definite FedEx rejection (or OAuth failure before creation) allows correction
  and retry. A timeout or uncertain response becomes **needs review**, blocking
  automatic resubmission because a shipment may already exist.
- The exact outbound request, raw successful response, tracking numbers, and PDF
  labels are persisted in the database. Responses are saved before label parsing
  and print queuing. Interrupted local response processing can be resumed without
  a second FedEx request.
- A record stuck in **creating** after an interrupted worker also needs manual
  reconciliation. Use its shipment UUID, sent as `x-customer-transaction-id`, and
  stored request to locate the transaction in FedEx. This identifier is for
  correlation; the code does not assume FedEx treats it as an idempotency key.
- Do not delete or reset an uncertain record just to retry. An admin should confirm
  the carrier outcome and reconcile any existing shipment/labels first. This pilot
  does not add carrier cancellation, label recovery, or an admin reset endpoint.
- Labels with expired workstation claims become failed instead of being silently
  reprinted. Check physical output before explicitly requesting a reprint.
- Labels and raw carrier responses contain recipient information. They are kept
  in authenticated app storage, not public URLs or logs. Database backups need to
  include the new tables.

## Verification

Backend tests mock FedEx and printers. They cover quantities, partial-order picks,
service exclusions, weight-only requests, package limits, optimistic revisions,
repeated/concurrent creation, uncertain outcomes, saved-response recovery, auth,
label mapping, printer routing, account-price sorting, quote expiry/invalidation,
cheapest selection, contact autofill, city/location matching, mixed-destination rejection,
per-order product ownership, order reservations, follower access, and QA revalidation for every member. The UI was checked in Chromium using
mocked API responses; actual FedEx sandbox and printer acceptance still require
the configured account and workstation.

FedEx references:

- https://developer.fedex.com/api/en-us/catalog/ship/v1/docs.html
- https://developer.fedex.com/wirc/json/api_groups/Ship/Shipment-Resource.json
- https://developer.fedex.com/api/en-us/catalog/rate/v1/docs.html
- https://cloudapi.inflowinventory.com/docs/api/swagger.json

Rate quotes use existing JSON draft storage. Run `alembic upgrade head` through `0026_shipping_delivery_merge` for contacts and combined-order reservations; existing single-order shipments are backfilled. Drafts saved with the earlier dimensions/manual
weight fields must be saved again before requesting prices.

## FedEx address-book CSV

Export the address book from your FedEx account using its Import/Export tools,
then import that CSV once in **Shipping → Manage address book**. No upload is needed per shipment. Up to 4 MB / 10,000 rows are accepted. Common
header spellings are recognized; the expected template is:

```csv
Contact ID,Contact Name,Company,Phone Number,Address Line 1,Address Line 2,City,State,Postal Code,Country,Residential
```

Contact ID is optional; preserve it when available so reimports update the same
contact. Without an ID, matching uses name/company/address. CSV formats without
recognized headers are rejected with an explanation rather than guessing column
positions. All rows are checked before database writes. The importer does not
require FedEx API credentials and can be tested offline with fictional contacts.

The native **FedExABExport.csv** format is also supported without editing:
`Nickname`, `FullName`, `FirstName`, `LastName`, `AddressOne`, `AddressTwo`, `Zip`,
`PhoneNumber`, `CountryCode`, and `ResidentialFlag` map to the saved contact.
Nickname is the displayed/searchable location label (for example, Dallas 3302),
while FullName remains the recipient printed on the shipment. If FullName is
blank, first/last name, company, or nickname can provide the recipient name.

Native `ResidentialFlag=R` means residential and `C` means commercial. Nine-digit
US ZIPs are formatted as ZIP+4 with leading zeroes preserved; city/location
matching accepts equivalent postal formatting and an omitted ZIP+4 extension.
Extra empty trailing columns from FedEx are allowed; unexpected non-empty extra
cells still stop the import. Unused billing, service, and notification preferences
are ignored: shipments continue to use the app's configured account and cheapest
eligible quoted service. Reimporting the same nickname updates its contact rather
than creating another record. No new database migration is needed.

Official FedEx address-book information: https://www.fedex.com/en-us/address-book.html

Packing allowance settings from older versions are ignored. Existing drafts must be saved again with a saved-contact selection or an authorized admin override before requesting new prices. Apply migration `0025_shipment_orders` before using city/location selection. It adds a unique reservation for each member order and backfills existing shipments.

## Matching named destinations

City matching ignores case, punctuation, and repeated spaces. State, ZIP, and
street details must agree when present; unit/address-line-2 matches take priority.
A location identifier such as 3302 can match the saved contact name when carried
in the delivery address or location text. A single compatible street-level contact
can match without a unit; multiple candidates or conflicting details require review.
The matcher intentionally does not fuzzy-match street abbreviations or guess a
building from city alone. Keep order addresses and imported contact names consistent.

Before quoting or submitting, the server rechecks every selected order's shipping
QA, unshipped quantities, and saved location. Changes require review. A member can
belong to only one shipment, and opening any member shows the complete shared plan.
Saving a draft without a member releases that member's reservation; submitted or
uncertain shipments cannot be edited to release orders. Label creation records
shared tracking in every member's audit history and leaves carrier handoff separate.

## Compatibility with current dev/main

This package is based on commit `347d92d60617671dd247e7aa11be1cda38181636`
(November 16, 2026), shared by dev and main when checked November 17. It preserves
the new delivery-photo evidence and attached-services QA features.

The upstream `0023_delivery_evidence` migration and the existing shipping
migrations form two branches from `0022_compat_bundle_ready`.
`0026_shipping_delivery_merge` joins them into one head without renaming or
rewriting existing revisions. Run `alembic upgrade head` normally. This supports
both a current dev database and a database that already tested the older shipping
ZIP. Do not remove, stamp over, or manually rerun either branch.
