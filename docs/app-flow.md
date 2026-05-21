# TechHub Delivery Workflow App

**Product Requirements Document (PRD)**

---

## 1. Overview

The TechHub Delivery Workflow App is an internal system that manages the full lifecycle of hardware orders from **picked** in inFlow through **delivery, signature capture, QA documentation, and final fulfillment**.

The goal is to replace fragmented scripts, Google Forms, and manual document handling with a **single, auditable, end-to-end workflow**.

---

## 2. Goals

- Centralize all delivery prep, execution, and documentation in one app
- Enforce required steps before delivery (tagging, picklist, QA)
- Eliminate Google Forms dependency
- Provide clear delivery run tracking and accountability
- Ensure signed and QA'd documentation is bundled and verified before inFlow fulfillment
- Reduce human error and missing documentation
- Support both local delivery and external shipping workflows

---

## 3. Non-Goals

- Route optimization or GPS tracking
- External recipient-facing portal
- Fully automated QA validation (QA remains human-driven)
- Replacing inFlow as the source of truth for inventory
- Carrier API integrations (manual tracking number entry)

---

## 4. Order Lifecycle & Status Model

### Order Classification

Orders are classified based on delivery method selected during QA:
- **Local Delivery**: Campus deliveries within Bryan/College Station
- **Shipping**: External deliveries via carrier (FedEx, UPS, etc.)

### Status Flows

**Local Delivery Orders:**
```
Picked → QA → Pre-Delivery → In Delivery → Delivered
```

**Shipping Orders:**
```
Picked → QA → Pre-Delivery → Shipping → Delivered
```

### Status Definitions

| Status | Description |
|--------|-------------|
| **Picked** | Order synced from inFlow after being picked. Awaiting prep steps. |
| **QA** | QA checklist in progress (asset tagging, picklist, QA form). |
| **Pre-Delivery** | All prep steps complete. Ready for delivery assignment or shipping. |
| **In Delivery** | Assigned to active local delivery run. Runner transporting to recipient. |
| **Shipping** | In shipping workflow (Work Area → Dock → Shipped to Carrier). |
| **Delivered** | Successfully delivered or shipped. Terminal state. |
| **Issue** | Problem encountered. Requires manual review and resolution. |

---

## 5. User Roles

| Role | Capabilities |
|------|--------------|
| **Technician / Runner** | Asset tagging, picklist generation, QA completion, delivery runs, signature capture, shipping processing |
| **Shipping Coordinator** | Prepare orders for shipping, coordinate with carriers, track shipping status |
| **Admin / Lead** | Verify completed orders, finalize delivery runs, trigger inFlow fulfillment, system configuration |

---

## 6. Functional Requirements

### 6.1 Order Ingest (inFlow → App)

**Sync Methods:**
- Automatic polling (configurable interval, default 20 minutes)
- Real-time webhooks (with polling fallback)

**Process:**
1. Fetch orders with `inventoryStatus="started"` from inFlow
2. Extract order metadata (ID, recipient, location, PO number)
3. Parse order remarks for alternative delivery locations
4. Extract building codes using ArcGIS service
5. Create or update order with `status = Picked`

---

### 6.2 Picked → Pre-Delivery Gate (Required Steps)

#### 6.2.1 Asset Tagging

- App provides "Asset Tag" action per order
- System records: Tag IDs, Technician, Timestamp, Success/failure
- **Blocking requirement:** Order cannot advance without successful tagging

#### 6.2.2 Picklist Generator

- App generates picklist PDF from inFlow order data
- Contains: Order header, items, quantities, serials, signature line
- Saved to SharePoint storage, linked to order
- **Blocking requirement:** Picklist must exist before QA

#### 6.2.3 QA Checklist

- In-app checklist replacing Google Form
- Items: Order verification, asset tags, packaging, combined order-details / electronic documentation check, labeling
- Includes **delivery method selection** (Delivery vs Shipping)
- Records: Responses, technician, timestamp, method
- **Blocking requirement:** QA must be completed to proceed

#### 6.2.4 Transition to Pre-Delivery

Once all three steps complete:
- Order status → **Pre-Delivery**
- Ready for delivery run assignment or shipping workflow

---

### 6.3 Delivery Run Management

#### Create Delivery Run

- Only **Pre-Delivery** orders selectable
- Assign: Runner, Vehicle (van/golf_cart), Orders
- System generates run name based on time of day
- Records: Start time, DeliveryRun ID

**On creation:**
- Orders transition to `In Delivery` status
- Teams notification sent to recipients
- WebSocket broadcast to all connected clients

#### Complete Delivery Run

**Requirements:**
- All orders in "Delivered" status
- All orders have signatures captured

**Process:**
- Validate all orders delivered
- Bulk mark orders as fulfilled in inFlow
- Record success/failure per order
- Close run with completion timestamp
- Auto check the vehicle back in when the final delivery is completed


---

### 6.4 Shipping Workflow

#### 6.4.1 Classification

Orders are classified as shipping during QA when technician selects "Shipping" method.

#### 6.4.2 Shipping Stages

```
Work Area → At Dock → Shipped to Carrier → Delivered
```

| Stage | Description |
|-------|-------------|
| **Work Area** | Initial stage. Order ready for shipping preparation. |
| **At Dock** | Order physically prepared, ready for carrier pickup. |
| **Shipped to Carrier** | Handed to carrier with tracking information. |

#### 6.4.3 Stage Transitions

- Sequential progression required (no stage skipping)
- Carrier name required for "Shipped to Carrier"
- Tracking number optional but recommended
- Automatic transition to "Delivered" upon shipping confirmation

---

### 6.5 Local Delivery & Signature Capture

1. Runner opens order's picklist in PDF viewer
2. Recipient signs using stylus/touch input
3. Signed picklist saved as new document version
4. Order transition to "Delivered" status

**Requirement:** Signature must be captured to complete delivery.

---

### 6.6 Document Bundling

After signature capture:
- System bundles: Signed picklist, QA form
- Creates folder structure in SharePoint:
```
delivery-storage/
├── picklists/
│   └── TH3950.pdf
├── qa/
│   └── TH3950_qa.json
└── signed/
    └── TH3950_signed.pdf
```

---

### 6.7 Order Completion & inFlow Fulfillment

#### Local Delivery Completion

1. Staff verifies all orders in run are delivered
2. Click "Complete Delivery" on Delivery Run
3. System validates all orders properly delivered
4. Bulk marks orders as fulfilled in inFlow
5. Records success/failure per order
6. Closes run with completion timestamp

#### Shipping Completion

1. Shipping coordinator confirms order shipped
2. Updates shipping status with carrier and tracking
3. Order transitions to "Delivered" status
4. System marks order as fulfilled in inFlow

---

## 7. Key Screens / UX Modules

### Orders Dashboard
- Status tabs: Picked / QA / Pre-Delivery / In Delivery / Shipping / Delivered / Issue
- Search: Order ID, recipient, location, PO number
- Quick actions: View details, transition status

### QA Checklist Page
- Order selection with filtering
- In-app checklist form
- Delivery method selection
- Submit and advance to Pre-Delivery

### Delivery Dashboard
- Live delivery run tracking
- Create new delivery runs
- View active and completed runs
- Real-time WebSocket updates

### Delivery Run Detail
- Run information (runner, vehicle, timing)
- Order list with status tracking
- Individual order transitions
- Run completion functionality

### Shipping Page
- Orders in shipping workflow stages
- Stage transitions with carrier info
- Tracking number capture

### Order Detail View
- Complete order metadata
- Prep step status (tagged, picklist, QA)
- Audit trail history
- Document links

### Document Signing
- PDF viewer with signature overlay
- Stylus/touch input capture
- Save and download signed document

### Admin Panel
- System status overview
- Service health indicators
- Webhook management
- Testing tools (sync, email, Teams)

---

## 8. Data Model

### Order

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| inflow_order_id | String | Order number from inFlow |
| status | Enum | Picked, QA, PreDelivery, InDelivery, Shipping, Delivered, Issue |
| recipient_name | String | Recipient name |
| recipient_contact | String | Email address |
| delivery_location | String | Building code or address |
| qa_method | String | "Delivery" or "Shipping" |
| tagged_at | DateTime | Asset tagging timestamp |
| picklist_generated_at | DateTime | Picklist generation timestamp |
| qa_completed_at | DateTime | QA completion timestamp |
| signature_captured_at | DateTime | Signature capture timestamp |
| shipping_workflow_status | Enum | work_area, dock, shipped |
| carrier_name | String | FedEx, UPS, etc. |
| tracking_number | String | Carrier tracking number |

### DeliveryRun

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | String | Generated name (e.g., "Morning Run 1") |
| runner | String | Assigned runner |
| vehicle | Enum | van, golf_cart |
| status | Enum | Active, Completed, Cancelled |
| orders | Relationship | Assigned orders |

### AuditLog

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| order_id | UUID | Foreign key to Order |
| from_status | String | Previous status |
| to_status | String | New status |
| changed_by | String | User who made change |
| timestamp | DateTime | When change occurred |

---

## 9. Integrations

### inFlow
- Order ingest (picked orders with pickLines)
- Order classification (delivery vs shipping)
- Fulfillment confirmation (pick/pack/ship lines)
- Webhook subscriptions for real-time updates

### Microsoft Graph API
- Email sending with PDF attachments
- SharePoint file storage
- Service Principal authentication

### Teams (via Power Automate)
- Delivery notification messages
- SharePoint queue folder monitoring
- Recipient chat messaging

### ArcGIS
- Building code extraction
- Address to building mapping
- Campus location intelligence

### SharePoint
- Document storage (picklists, QA, signed docs)
- Notification queue folder
- Completed order bundles

---

## 10. Error & Exception Handling

- Prep step failures block status transitions
- Orders can be marked "Issue" with reason
- Issue orders can return to Picked or PreDelivery after resolution
- inFlow fulfillment failures keep run open with alerts
- Webhook failures fall back to polling sync
- Service failures (email, Teams) logged but non-blocking

---

## 11. Success Metrics

### Delivery Metrics
- % of deliveries completed without missing documentation
- Average time from Picked → Delivered
- On-time delivery rate
- Customer signature capture rate

### Shipping Metrics
- % of shipping orders completed without issues
- Average time from Picked → Shipped
- Carrier on-time pickup rate
- Shipping documentation completeness

### Overall
- Time from Picked → Pre-Delivery readiness
- Reduction in manual verification errors
- Zero missing QA cases at fulfillment
- Order classification accuracy (delivery vs shipping)
- System uptime and reliability
