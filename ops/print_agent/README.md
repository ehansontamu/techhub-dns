# Picklist Print Agent

Fixed desktop agent for automatically printing first-time picklists and manually queued reprints.

## Configuration

Copy `.env.example` to `.env` and set:

- `API_BASE_URL`
- `AGENT_TOKEN`
- `PRINTER_NAME`
- `SUMATRA_PDF_PATH`
- `POLL_SECONDS`
- `ERROR_RETRY_SECONDS` (optional, default `5`)
- `PRINT_TIMEOUT_SECONDS` (optional, default `120`)
- `SPOOL_DIR`

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python agent.py
```

The agent joins the `print_jobs` Socket.IO room, wakes immediately on `print_job_available`, and still polls as a fallback in case the websocket is disconnected.
