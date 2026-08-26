# Compatibility editor seed

`compatibility_initial.json` is the bootstrap dataset for the collaborative
compatibility editor. After migration `0020_add_compatibility_editor` creates
an empty singleton state, the first authenticated admin request imports this
file exactly once. A non-empty database is never replaced automatically.

For an explicit import after applying migrations, run from `backend/`:

```bash
python scripts/import_compatibility_editor.py
```

`--replace` intentionally replaces live compatibility data and should only be
used for a planned recovery.

Set `COMPATIBILITY_EDITOR_WEBDAV_FOLDER_URL` to the existing WebDAV directory.
The publisher appends the fixed `compatibility_superapp.json` filename and does
not write any other WebDAV resource. Enable the scheduler process for the
five-minute reconciliation safety net; request-triggered publishing still
runs after normal edits.
