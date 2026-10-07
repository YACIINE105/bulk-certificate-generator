# Bulk Certificate Generator

A small Python backend for the assignment: submit many recipients in one request,
track progress, and download each generated certificate. Uses FastAPI, SQLite,
and one printable HTML template. No external database or queue service is needed.

## Setup and run

Run these commands in Ubuntu/WSL from the project directory. Install `uv` first
if it is not already available. Python 3.12 or newer is required.

```bash
uv venv .venv
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000/docs for the interactive API. Keep the default single
worker: startup recovery assumes one application process using the data directory.
Use `--reload` only during development; reloading can interrupt active work.

## Submit a batch

```bash
curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  --data-binary @example_request.json
```

Example input is in `example_request.json`. Required batch fields are `title`,
`course`, `issuer`, `issued_on` (ISO date), and `recipients` (1 to 10,000 entries).
Each recipient needs a nonblank `name` and valid `email`. Unknown fields are rejected.
Blank strings and invalid types are rejected. Names are limited to 150 characters;
course names to 200; title and issuer to 150. Whitespace is trimmed.

The API immediately returns HTTP 202 with a response shaped like:

```json
{
  "id": "<job-id>",
  "status": "queued",
  "total": 2,
  "status_url": "/jobs/<job-id>"
}
```

Invalid batch information returns HTTP 422. Invalid recipient entries are recorded
as failures within the job, so valid entries can still receive certificates. Each
entry is handled independently; repeated names or email addresses are permitted.

## Track progress and retrieve certificates

```bash
curl http://localhost:8000/jobs/<job-id>
curl 'http://localhost:8000/jobs/<job-id>?offset=100&limit=100'
curl -OJ http://localhost:8000/certificates/<certificate-id>
```

Replace placeholders with returned IDs. The job response includes `total`,
`completed`, `failed`, `pending`, `processed`, `progress` (0 to 100), and recipient
results with status, error, and `download_url`. Counts cover the whole job even
when recipient results are paginated. The default page size is 100; maximum 1,000.
Positions are zero-based and match the input order.

Job statuses: `queued`, `processing`, `completed`, `partial_failure`, `failed`,
and `interrupted`. `completed` means every certificate succeeded; `partial_failure`
means a mix; `failed` means no certificate succeeded. Progress counts successful
and failed entries as processed, so a failed job can have 100% progress.

Recipient statuses: `pending`, `completed`, `failed`. Downloads are available
only for completed entries. Unknown IDs return 404, malformed IDs return 422,
and unavailable certificates return 409. Missing generated files return 404.

Downloaded files are UTF-8 HTML certificates. Open one in a browser to view or
print it; the browser's **Print → Save as PDF** can produce a PDF. The backend
itself generates HTML, which the assignment explicitly allows. The template
contains the recipient name, email, course, title, issuer, date, and unique ID.

## Tests and code checks

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use temporary databases and files. They cover job creation, batch and
recipient validation, template generation and escaping, Unicode names, progress
during processing, failure isolation, downloads, pagination, bulk limits, missing
files, and restart behavior.

## Design decisions

- **FastAPI** handles request validation and provides API documentation.
- **SQLite** stores jobs and per-recipient results using parameterized SQL.
  Standard-library `sqlite3` keeps the application small. WAL mode and a busy
  timeout support polling while background tasks update results.
- **BackgroundTasks** returns the acceptance response before generation. A batch
  generates certificates one at a time in a background thread and saves each result,
  allowing progress checks and isolating individual generation failures.
- **HTML** avoids PDF/font dependencies, handles Unicode with browser fonts, and
  is easy to customize in `app/certificates.py`. User values are escaped, and file
  names use server-generated UUIDs. Files are replaced atomically before success
  is recorded. Long content may span multiple printed pages.
- **Local persistence** stores the database at `data/certificates.sqlite3` and
  certificates under `data/certificates/`. Set `CERTIFICATE_DATA_DIR` to change the
  location. Completed results survive restarts. On startup, unfinished jobs are
  marked `interrupted` and pending recipients fail with an explicit reason;
  already completed certificates remain downloadable. There is no automatic retry.
- **Scope** is a single-process assignment/demo without authentication or email
  delivery. Background work is not a durable queue and has no backpressure across
  simultaneous jobs. A production service would use authentication, a durable
  worker queue, storage limits, and shared file storage before scaling out.

## Files

- `app/main.py`: API routes and background job processing.
- `app/schemas.py`: batch and recipient validation.
- `app/database.py`: SQLite connections, schema, restart recovery.
- `app/certificates.py`: the predefined certificate template.
- `tests/test_api.py`: automated tests.
- `example_request.json`: a ready-to-submit request.
- `uv.lock`: reproducible dependency versions.
