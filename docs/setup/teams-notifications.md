# Teams Notifications Setup (Power Automate)

This guide explains how to create a Power Automate flow that monitors a SharePoint folder and sends Teams chat messages to order recipients.

## Overview

The TechHub backend queues notification requests as JSON files to a SharePoint folder. Power Automate monitors this folder, reads the notification data, sends a Teams message, and deletes the processed file.

### Architecture

```
Backend -> SharePoint Queue Folder -> Power Automate Flow -> Teams Message
              └── notification_TH4013_*.json
```

## Prerequisites

- Microsoft 365 account with Power Automate access
- SharePoint site with write access
- Teams messaging permissions

## Step 1: Create the SharePoint Queue Folder

1. Navigate to your SharePoint site (e.g., `https://tamucs.sharepoint.com/teams/Team-TechHub`)
2. Go to Documents -> General -> delivery-storage
3. Create a new folder named `notifications-queue`

Full path: `General/delivery-storage/notifications-queue`

## Step 2: Create the Power Automate Flow

1. Go to [Power Automate](https://make.powerautomate.com)
2. Sign in with your organizational account
3. Click Create -> Automated cloud flow
4. Name: `TechHub Delivery Notification`
5. Select trigger: When a file is created (properties only) (SharePoint)
6. Click Create

## Step 3: Configure the SharePoint Trigger

Configure the trigger:

| Field | Value |
|-------|-------|
| Site Address | `https://tamucs.sharepoint.com/teams/Team-TechHub` |
| Library Name | `Documents` |
| Folder | `/General/delivery-storage/notifications-queue` |

## Step 4: Add "Get file content" Action

1. Click + New step
2. Search for SharePoint
3. Select Get file content
4. Configure:
   - Site Address: Same as trigger
   - File Identifier: Select Identifier from dynamic content

## Step 5: Add "Parse JSON" Action

1. Click + New step
2. Search for Parse JSON (Data Operation)
3. Configure:
   - Content: Select File Content from dynamic content
   - Schema: Use the schema below

### JSON Schema

```json
{
    "type": "object",
    "properties": {
        "id": { "type": "string" },
        "type": { "type": "string" },
        "recipientEmail": { "type": "string" },
        "recipientName": { "type": "string" },
        "orderNumber": { "type": "string" },
        "deliveryRunner": { "type": "string" },
        "message": { "type": "string" },
        "estimatedTime": { "type": "string" },
        "createdAt": { "type": "string" }
    },
    "required": ["recipientEmail", "orderNumber", "deliveryRunner"]
}
```

## Step 6: Add a Notification-Type Condition

Before the Teams message action, add a **Condition**:

```text
@equals(body('Parse_JSON')?['type'], 'inventory_reorder_notification')
```

- **If yes:** use the bulk-order message below.
- **If no:** retain the existing delivery message action.

## Step 7: Add Teams Message Actions

1. Click + New step
2. Search for Microsoft Teams
3. Select Post message in a chat or channel
4. Configure:
   - Post as: Flow bot
   - Post in: Chat with Flow bot
   - Recipient: `recipientEmail` (from dynamic content)
   - Message for the **No** (delivery) branch: Use the template below

### Message Template

```
Your TechHub Order is On Its Way!

Hi @{body('Parse_JSON')?['recipientName']},

Your order **@{body('Parse_JSON')?['orderNumber']}** is now out for delivery!

Delivery Runner: @{body('Parse_JSON')?['deliveryRunner']}
Estimated Arrival: @{body('Parse_JSON')?['estimatedTime']}

Please ensure someone is available to receive the delivery.

---
TechHub Technology Services
Texas A&M University
```

For the **Yes** (bulk-order) branch, use this message instead:

```
@{body('Parse_JSON')?['message']}
```

This displays, for example:

```
Bulk BigCommerce Order Alert

BigCommerce order BC-12345 is a bulk order with 10 items.
```

## Step 8: Add "Delete file" Action

After sending the Teams message, delete the processed file:

1. Click + New step
2. Search for SharePoint
3. Select Delete file
4. Configure:
   - Site Address: Same as trigger
   - File Identifier: Select Identifier from trigger dynamic content

## Step 9: Save and Test

1. Click Save
2. Test by uploading a sample JSON file to the SharePoint queue folder:

### Sample Test File

```json
{
    "id": "test_notification_001",
    "type": "delivery_notification",
    "recipientEmail": "your-email@tamu.edu",
    "recipientName": "Test User",
    "orderNumber": "TEST-001",
    "deliveryRunner": "Test Runner",
    "estimatedTime": "Shortly",
    "createdAt": "2026-01-21T15:00:00"
}
```

3. Upload to SharePoint queue folder
4. Check flow run history
5. Verify Teams message received

## Step 10: Configure TechHub Backend

Add to your `.env` file:

```env
TEAMS_RECIPIENT_NOTIFICATIONS_ENABLED=true
SHAREPOINT_ENABLED=true
SHAREPOINT_SITE_URL=https://tamucs.sharepoint.com/teams/Team-TechHub
SHAREPOINT_FOLDER_PATH=General/delivery-storage
TEAMS_NOTIFICATION_QUEUE_FOLDER=notifications-queue
```

## Complete Flow Overview

```
When a file is created (SharePoint)
  -> Get file content (SharePoint)
  -> Parse JSON
  -> Condition: is inventory_reorder_notification?
      -> Yes: post bulk-order message in chat (Teams)
      -> No: post delivery message in chat (Teams)
  -> Delete file (SharePoint)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Flow not triggering | Check folder path matches exactly |
| Parse JSON failing | Verify JSON structure matches schema |
| Teams message not appearing | Check recipient has Teams and allows bot messages |
| Permission errors | Request Teams connector access from IT |
| File not deleted | Verify SharePoint delete permissions |

### Checking Flow History

1. Go to Power Automate
2. Click on your flow
3. Click Run history
4. Check for failed runs and error details

## Security Notes

- Flow runs with your permissions
- Consider using a service account for production
- JSON files contain recipient email addresses (PII)
- Files are deleted after processing

## Testing from Backend

Use the Admin panel to send a test notification:

1. Navigate to `/admin`
2. Find the Teams Notification section
3. Enter a test email address
4. Click "Test Teams Notification"
5. Check SharePoint folder for queued file
6. Verify flow processes the file
7. Confirm Teams message received
