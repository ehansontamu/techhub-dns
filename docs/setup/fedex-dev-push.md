# Apply and push the FedEx sandbox changes to dev

These instructions use a normal Git commit followed by a push to `dev`. A push has
no separate title or description: those belong to the **commit** you push.
The supplied `COMMIT-MESSAGE.txt` contains both, ready to copy.

## Starting with only the change-files ZIP

The change ZIP is not a cloned repository. Extract it somewhere separate from your
project. Do not create an empty Git repository in `changed-files/` and try to push it.
You need a clone of the existing repository first.

### 1. Clone with GitHub Desktop and select dev

1. Install/open [GitHub Desktop](https://desktop.github.com/) and sign in with the
   GitHub account that has write access to `ehansontamu/techhub-dns`.
2. Choose **File → Clone repository → URL** and enter:
   `https://github.com/ehansontamu/techhub-dns.git`.
3. Choose a local folder, such as `Documents/GitHub/techhub-dns`. This becomes your
   repository folder; it will contain `README.md`, `backend/`, and `frontend/`.
4. In **Current branch**, select **dev**. Fetch/pull available changes before
   applying the package. Confirm you are on `dev`, not `main`.
5. Use **Repository → Show in Explorer/Finder** to open that folder.

### 2. Check ignored files before applying the package

Open `.gitignore` in the cloned repository and confirm it contains `backend/.env`.
The checked `dev` branch already includes this entry. This package also ignores
`ops/print_agent/.env` and its Python cache, protecting the workstation token. Do this before copying or
applying any update files. No credentials are needed to apply or commit the package.

If using a terminal with Git installed, these read-only checks give extra confirmation:

```sh
git check-ignore backend/.env
git ls-files -- backend/.env
```

The first should print `backend/.env`; the second should print nothing. If the file
is already tracked, `git rm --cached --ignore-unmatch backend/.env` stops tracking
it while preserving the local file. Review that change before committing.

`backend/.env.example` is intentionally tracked and must contain placeholders only.
Do not commit a real `.env`, keys, passwords, certificates, or print-agent tokens.

### 3. Apply the appropriate patch

The package includes `techhub-fedex-sandbox.patch` (all shipping changes against
the stated baseline) and `techhub-fedex-update.patch` (changes from the immediately
previous downloadable package). Pick the one matching your starting point; do not
apply both. The baseline SHA and package-specific instructions are in `APPLY.md`.

From the cloned repository folder, in a terminal with Git installed, first run:

```sh
git branch --show-current
git status --short
git apply --check /path/to/techhub-fedex-sandbox.patch
```

Replace `/path/to/` with the extracted package path. Quote the path if it contains
spaces. For the incremental update, substitute `techhub-fedex-update.patch`.
If the check passes, apply the same patch:

```sh
git apply /path/to/techhub-fedex-sandbox.patch
```

The full package was prepared against a particular `main` commit, so a newer or
different `dev` may need conflicts resolved. If the check fails, stop and reconcile
the differences; do not overwrite existing dev work or force-push to bypass it.
[Install Git](https://git-scm.com/downloads) if the terminal does not recognize `git`.

For a file-copy workflow, the ZIP also contains `changed-files/`. Copy its **contents**
into the cloned repository, preserving paths. Do not copy the enclosing folder,
ZIP, or standalone patch files into the repository. Review every resulting change
in GitHub Desktop because copying full files can replace newer dev edits.
Do not apply the patch after copying the same files.

### 4. Review and commit in GitHub Desktop

1. Confirm **Current branch: dev**.
2. Review **Changes**, including the README, migrations, API/service code, frontend,
   print-agent changes, and tests. No runtime `.env` or credential values should be
   present. Do not include unrelated local work.
3. Open the package's `COMMIT-MESSAGE.txt`. Copy its first line into **Summary** and
   the remaining paragraphs into **Description**.
4. Select the intended files and choose **Commit to dev**.
5. Click **Push origin**. If direct pushes are blocked by repository rules, obtain
   the required permission or use the team's permitted process; don't bypass it.

Do not use GitHub's Upload Files workflow if you want a local commit and push.

### Terminal equivalent (after applying/reviewing the patch)

The exact staging list depends on which shipping changes you already have. Stage
only the intended files, then inspect them before committing:

```sh
git add .gitignore README.md docs/ backend/app/ backend/alembic/versions/ backend/tests/ frontend/src/ ops/print_agent/ backend/.env.example
git diff --cached --name-only
git diff --cached
```

This staging list is intended for a clean clone with only this package applied.
If you also have unrelated work under those folders, select files individually.
Check that no secrets or generated files have been included. Then use the supplied
message file from outside the repository:

```sh
git commit -F /path/to/COMMIT-MESSAGE.txt
git push origin dev
```

There is no separate `git push` name/description. The pushed commit carries the
Summary/Description above.

## What the dev push does

The repository's `.github/workflows/deploy-pythonanywhere.yml` triggers on a push
to `dev`. Its fast backend compilation check must pass before the remote dev
deployment runs. It targets:

- Site: https://dev-techhub.pythonanywhere.com
- Checkout: `/home/techhub/techhub-dns-dev`
- Branch: `dev`

Watch **GitHub → Actions → Deploy to PythonAnywhere** for the commit you pushed.
A pull request by itself runs checks but does not deploy. `main` has a separate
production deployment path. Do not push this sandbox feature to `main` for testing.

The checked deployment script runs Alembic migrations and builds the frontend.
This package adds migrations `0023_add_shipments`, `0024_shipping_contacts`, `0025_shipment_orders`, and
`0026_shipping_delivery_merge`.
Deployment success does not establish that FedEx credentials, permissions, or the
printer have been configured correctly.

## Dev-server configuration and acceptance testing

Someone with access to the **dev PythonAnywhere server** must configure these
privately in `/home/techhub/techhub-dns-dev/backend/.env`, not in GitHub:

- `FEDEX_ENABLED=true`
- `FEDEX_CLIENT_ID`, `FEDEX_CLIENT_SECRET`, `FEDEX_ACCOUNT_NUMBER` from the sandbox project
- `FEDEX_SHIPPER_JSON` with the sender's real contact/address details
- Existing `PICKLIST_PRINT_AGENT_TOKEN` for the workstation worker

The FedEx project needs Ship API and Rates and Transit Times API access. Configure
only sandbox credentials; the client hostname is fixed to FedEx sandbox. Restart
or reload the dev web app after private environment changes. GitHub write access
does not automatically grant access to these server settings.

For the Brother QL-1110NWB, update the existing **test workstation agent** to target
the dev URL and its matching agent token, keep the picklist printer under
`PRINTER_NAME`, and set `LABEL_PRINTER_NAME` to the Brother's exact Windows queue
name. Use correctly sized stock and validate physical label fit/barcode scanning.
Keep testing isolated from the production print worker.

First, sign in as an admin and open **Settings → Overview → System status →
FedEx Sandbox**, beside SharePoint. After the dev backend reloads, use **Refresh
system status**. Check enabled, key/secret/account set, and sender valid without
exposing values. Confirm a regular user cannot see or request the system-status
report. **Configured** checks loaded settings only; getting a price tests FedEx
authentication and Rates access.

After deployment, verify with non-production order data:

1. Select several QA-completed orders directly by order number, including orders
   with incomplete or different original destinations. Open their combined packing
   workspace, choose the one saved shipping contact for the batch, and verify each
   order's units, quantities and ownership. Create Box 3, Box 4 and further boxes
   repeatedly and verify all product selectors offer them. Empty boxes must not
   generate labels. Existing shipment members must reopen their shared shipment.
2. Weights are the sum of saved per-product weights; no packing allowance or manual weight/dimension inputs.
3. An admin imports FedEx contacts once through **Manage address book**. The contacts
   remain available on another shipment without another upload.
4. Standard users can ship only to saved contacts and cannot edit recipient fields.
   An admin can use an audited one-off override without changing the address book.
5. Rates return only the cheapest eligible supported USD service and its total.
6. A quote of $100.00 uses the ordinary confirmation; a quote over $100 requires
   **Hey, are you sure? → Yes, create shipment**. Cancelling creates nothing.
7. Creating the sandbox shipment saves labels/tracking and queues labels for the
   Brother. A reprint uses the same saved label without another FedEx shipment.
8. Creating/printing labels does not mark the order Delivered or record a real
   carrier handoff. Sandbox labels must not be used for actual shipping.

See [the full shipping setup and recovery guide](../fedex-shipping.md) for CSV
headers, sandbox variables, printer configuration, quote expiry, and handling
uncertain carrier outcomes. Public/private `.env` settings and real hardware were
not verified by the automated development checks.

## November 23 update: order-first selection and dynamic boxes

The current update targets dev commit
`0ddabd3f7ed9cbefcbb658d4f6e10d7c8bc1ddc8`. Pull dev before applying the supplied
order-first patch. This includes the automatic saved-contact picker, order-number
multi-selection, explicit batch recipient, repeated numbered-box creation, and admin-only FedEx
configuration diagnostics in Settings System status.
It uses existing shipment/member storage and adds no database migration. Existing
migrations through `0026_shipping_delivery_merge` remain necessary for shipping.

Use the new order-first commit message supplied with the ZIP. If you already
applied the preceding saved-contact-picker ZIP locally, use the incremental patch
for that starting point; do not reapply both full patches. Preserve local work and
resolve patch-check errors before continuing.
