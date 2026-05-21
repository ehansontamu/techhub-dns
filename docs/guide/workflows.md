# Workflows and Lifecycle

## Order Synchronization

### Inflow API Integration

The system synchronizes orders from Inflow Cloud inventory management through two mechanisms:

#### Polling Sync

**Service**: `backend/app/services/inflow_service.py`
**Scheduler**: `backend/app/scheduler.py`

**Process**:
1. APScheduler triggers sync at configured interval (default: 20 minutes)
2. Fetches orders with `inventoryStatus="started"` (picked orders)
3. Processes up to 3 pages (100 orders per page)
4. For each order:
   - Extracts order metadata (ID, recipient, location, PO number)
   - Parses order remarks for alternative delivery locations
   - Extracts building code using ArcGIS
   - Creates or updates order in database
   - Sets initial status to `Picked`

**Configuration**:
```env
INFLOW_POLLING_SYNC_ENABLED=true
INFLOW_POLLING_SYNC_INTERVAL_MINUTES=20
```

#### Webhook Sync

**Endpoint**: `POST /api/inflow/webhook`

Real-time order updates via Inflow webhooks:

1. Inflow sends webhook on order creation/update
2. Backend verifies webhook signature
3. Processes order data immediately
4. Falls back to polling if webhooks fail

**Configuration**:
```env
INFLOW_WEBHOOK_ENABLED=true
INFLOW_WEBHOOK_URL=https://your-domain/api/inflow/webhook
INFLOW_WEBHOOK_EVENTS=["orderCreated","orderUpdated"]
INFLOW_WEBHOOK_AUTO_REGISTER=true
```

**Webhook Management**:
```bash
# List webhooks
python scripts/manage_inflow_webhook.py list

# Register webhook
python scripts/manage_inflow_webhook.py register \
  --url https://your-domain/api/inflow/webhook \
  --events orderCreated,orderUpdated

# Reset webhook
python scripts/manage_inflow_webhook.py reset \
  --url https://your-domain/api/inflow/webhook \
  --events orderCreated,orderUpdated
```

## Location Intelligence

### Building Code Extraction

**Service**: `backend/app/utils/building_mapper.py`

Maps addresses to TAMU building abbreviations using ArcGIS:

**Process**:
1. Check if location string contains known building code (e.g., "LAAH 424" -> "LAAH")
2. If not found, query ArcGIS service with address
3. Match against building data attributes
4. Return building abbreviation or original address

**ArcGIS Endpoint**:
```
https://gis.cstx.gov/csgis/rest/services/IT_GIS/ITS_TAMU_Parking/MapServer/3/query
```

**Caching**: Building data cached for 24 hours to reduce API calls.

**Common Building Codes**:
| Code | Building |
|------|----------|
| ACAD | Academic Building |
| ZACH | Zachry Engineering Education Complex |
| LAAH | Liberal Arts and Humanities |
| MPHY | Mitchell Physics |
| BLOC | Blocker Building |
| HRBB | H.R. Bright Building |

### Order Remarks Parsing

**Service**: `backend/app/services/order_service.py`

Extracts alternative delivery locations from order notes:

**Patterns Matched**:
- "deliver to [location]"
- "delivery to [location]"
- "deliver at [location]"
- "located at [location]"
- "alternative location: [location]"

**Example**:
- Remarks: "Please deliver to LAAH 424"
- Extracted Location: "LAAH 424"
- Building Code: "LAAH"

## Order Status Workflow

### Status Definitions

| Status | Description |
|--------|-------------|
| `Picked` | Order synced from Inflow, awaiting prep steps |
| `QA` | QA checklist in progress |
| `PreDelivery` | All prep steps complete, ready for assignment |
| `InDelivery` | Assigned to active delivery run |
| `Shipping` | In shipping workflow (external delivery) |
| `Delivered` | Successfully delivered (terminal state) |
| `Issue` | Problem encountered, requires resolution |

### Operational Status Specification

This is the practical "what do we do next?" view for staff and leads.

| Status | What it means | Usually needs to happen next |
|--------|---------------|------------------------------|
| `Picked` | Order exists in TechHub and has been pulled from Inflow. | Asset tagging, picklist generation, and QA setup. |
| `QA` | The order is being prepared and checked. | Complete checklist and decide delivery vs shipping. |
| `PreDelivery` | Prep work is done and the order is ready to move. | Add to delivery run or route into shipping workflow. |
| `InDelivery` | The order is on a vehicle for a local delivery. | Capture signature / complete delivery. |
| `Shipping` | The order is in the dock / carrier workflow. | Move through shipping stages until carrier handoff. |
| `Delivered` | The order is finished. | No further action unless a correction is needed. |
| `Issue` | Something blocked the normal workflow. | Resolve the issue, then move back to the appropriate prep state. |

### Workflow Flowchart

```mermaid
flowchart LR
    Picked --> QA
    QA --> PreDelivery
    PreDelivery --> InDelivery
    PreDelivery --> Shipping
    InDelivery --> Delivered
    Shipping --> Delivered
    QA --> Issue
    PreDelivery --> Issue
    InDelivery --> Issue
    Shipping --> Issue
    Issue --> Picked
    Issue --> PreDelivery
```

### Workflow Transitions

**Local Delivery**:
```
Picked -> QA -> PreDelivery -> InDelivery -> Delivered
              -> Issue -> Picked/PreDelivery
```

**Shipping**:
```
Picked -> QA -> PreDelivery -> Shipping -> Delivered
              -> Issue -> Picked/PreDelivery
```

### Transition Validation

**Service**: `backend/app/services/order_service.py`

Valid transitions enforced by `_is_valid_transition()`:

| From | Valid To |
|------|----------|
| Picked | QA, PreDelivery, Issue |
| QA | PreDelivery, Issue |
| PreDelivery | InDelivery, Shipping, Issue |
| InDelivery | Delivered, Issue |
| Shipping | Delivered, Issue |
| Issue | Picked, PreDelivery |
| Delivered | (terminal) |

## QA Checklist System

### Overview

In-app quality assurance replacing Google Forms with workflow routing.

**Page**: `frontend/src/pages/OrderQAChecklist.tsx`
**Endpoint**: `POST /api/orders/{order_id}/qa`

### Checklist Items

| Item | Description |
|------|-------------|
| Order Verification | Confirm order details match Inflow |
| Asset Tag Check | Verify asset tags match serial numbers |
| Order Details / Electronic Docs | Confirm the order details template was sent and electronic packing slip was saved |
| Packaging Verification | Check proper packaging |
| Packing Slip | Verify packing slip accuracy |
| Box Labeling | Verify box labels |
| QA Signature | Technician sign-off |
| **Delivery Method** | Select "Delivery" or "Shipping" |

### Workflow Routing

The **Delivery Method** selection determines workflow path:

- **"Delivery"**: Order follows local delivery workflow -> `InDelivery`
- **"Shipping"**: Order follows shipping workflow -> `Shipping`

### Prep Step Gating

Orders cannot advance to PreDelivery without completing:
1. Asset tagging (`tagged_at` not null)
2. Picklist generation (`picklist_generated_at` not null)
3. QA completion (`qa_completed_at` not null)

## Delivery Run Management

### Overview

Groups multiple orders into coordinated delivery runs with vehicle and runner tracking.

**Service**: `backend/app/services/delivery_run_service.py`
**Pages**: `frontend/src/pages/delivery/Dispatch.tsx`, `frontend/src/pages/delivery/Fleet.tsx`

### Delivery Run Model

```python
class DeliveryRun:
    id: String(36)           # UUID
    name: String             # Auto-generated (e.g., "Morning Run 1")
    runner: String           # Assigned runner name
    vehicle: Enum            # 'van' or 'golf_cart'
    status: Enum             # 'Active', 'Completed', 'Cancelled'
    start_time: DateTime     # Run creation time
    end_time: DateTime       # Completion time
    orders: Relationship     # Assigned orders
```

### Run Creation Process

1. Select Pre-Delivery orders from queue
2. Select vehicle (runner identity is derived from authenticated session)
3. Ensure the selected vehicle is checked out by the current user with checkout type `delivery_run`
3. System generates run name based on time:
   - Morning (before 12:00): "Morning Run N"
   - Afternoon (12:00-17:00): "Afternoon Run N"
   - Evening (after 17:00): "Evening Run N"
4. Orders transition to `InDelivery` status
5. WebSocket broadcasts update to all clients

### Vehicle Management

| Vehicle | Description |
|---------|-------------|
| `van` | Full-size delivery van |
| `golf_cart` | Campus golf cart |

**Availability Check**: Only one active run per vehicle.

### Vehicle Checkout Workflow

Vehicles are checked out independently from delivery runs.

- Checkout types:
  - `delivery_run`: eligible to start a delivery run
  - `other`: non-delivery usage; requires a non-empty `purpose`
- Starting a delivery run requires an active checkout owned by the current user with type `delivery_run`.
- If a vehicle is checked out as `other`, the user must check it in and then check it out again as `delivery_run` (no in-place conversion).

### Run Completion

Requirements for completion:
1. All orders in `Delivered` status
2. All orders have signatures (for local delivery)

On completion:
- Run status -> `Completed`
- End time recorded
- Orders marked fulfilled in Inflow (best-effort)

## Shipping Workflow

### Overview

Structured workflow for orders requiring external shipping.

**Page**: `frontend/src/pages/Shipping.tsx`

### Shipping Stages

```
Work Area -> At Dock -> Shipped to Carrier -> Delivered
```

| Stage | Description |
|-------|-------------|
| `work_area` | Initial stage, order ready for shipping prep |
| `dock` | Order physically prepared, awaiting carrier |
| `shipped` | Handed to carrier with tracking info |

### Stage Transitions

**Blocking Requirements**:
- Cannot skip stages (must progress sequentially)
- Cannot proceed to Shipped without being at Dock
- Carrier name required for Shipped transition
- Tracking number optional but recommended

### Shipping Data Model

```python
# Order shipping fields
shipping_workflow_status: Enum       # work_area, dock, shipped
shipping_workflow_status_updated_at: DateTime
shipping_workflow_status_updated_by: String
shipped_to_carrier_at: DateTime
shipped_to_carrier_by: String
carrier_name: String                 # FedEx, UPS, etc.
tracking_number: String              # Carrier tracking number
```

### Automatic Delivery

When order reaches `shipped` status, system automatically transitions to `Delivered`.

## PDF Generation

### Overview

ReportLab-based PDF generation for picklists and order details.

**Service**: `backend/app/services/pdf_service.py`

### Picklist Generation

**Endpoint**: `POST /api/orders/{order_id}/picklist`

**Contents**:
- Order header (PO number, customer info, shipping address)
- Item details (products, SKUs, quantities, serial numbers)
- Smart filtering (only unshipped items)
- Order remarks
- Signature line

**Storage**: `STORAGE_ROOT/picklists/{order_number}.pdf`

### Order Details PDF

**Endpoint**: `GET /api/orders/{order_id}/order-details.pdf`

**Contents**:
- TAMU Technology Services header with logo
- Barcode for order number
- Billing and shipping addresses
- Line items with prices
- Subtotals and totals
- Order remarks

**Features**:
- In-browser viewing
- Download option
- Email delivery to recipient

### PDF Email Delivery

**Endpoint**: `POST /api/orders/{order_id}/send-order-details`

Generates Order Details PDF and emails to recipient with:
- Professional HTML email template
- Plain text fallback
- PDF attachment

## Document Signing

### Overview

In-app PDF signature capture for delivery confirmation.

**Page**: `frontend/src/pages/DocumentSigningPage.tsx`

### Features

- Load PDF documents from storage
- Stylus/touch signature input
- Overlay signature on PDF
- Save signed version
- Download signed document

### Process

1. Load picklist PDF for order
2. Recipient draws signature
3. Signature embedded in PDF
4. Signed document saved to storage
5. Order `signature_captured_at` updated
