import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.certificates import generate_certificate
from app.database import connect, initialize
from app.schemas import JobRequest, Recipient

logger = logging.getLogger(__name__)


def process_job(database: Path, output_dir: Path, job_id: str):
    with connect(database) as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        db.execute("UPDATE jobs SET status = 'processing' WHERE id = ?", (job_id,))
        recipients = db.execute(
            "SELECT * FROM certificates WHERE job_id = ? AND status = 'pending' ORDER BY position",
            (job_id,),
        ).fetchall()
    for row in recipients:
        try:
            generate_certificate(
                job, json.loads(row["recipient"]), row["id"], output_dir / f"{row['id']}.html"
            )
            status, error = "completed", None
        except Exception:
            logger.exception("Certificate generation failed for %s", row["id"])
            status, error = "failed", "Certificate generation failed"
        with connect(database) as db:
            db.execute(
                "UPDATE certificates SET status = ?, error = ? WHERE id = ?",
                (status, error, row["id"]),
            )
    with connect(database) as db:
        counts = dict(
            db.execute(
                "SELECT status, COUNT(*) FROM certificates WHERE job_id = ? GROUP BY status",
                (job_id,),
            ).fetchall()
        )
        status = "completed"
        if counts.get("failed"):
            status = "partial_failure" if counts.get("completed") else "failed"
        db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def create_app(data_dir: Path | None = None):
    data_dir = Path(data_dir or os.getenv("CERTIFICATE_DATA_DIR", "data"))
    database = data_dir / "certificates.sqlite3"
    output_dir = data_dir / "certificates"

    @asynccontextmanager
    async def lifespan(app):
        output_dir.mkdir(parents=True, exist_ok=True)
        initialize(database)
        yield

    app = FastAPI(title="Bulk Certificate Generator", lifespan=lifespan)

    @app.post("/jobs", status_code=202)
    def create_job(request: JobRequest, background_tasks: BackgroundTasks):
        job_id = str(uuid4())
        rows = []
        for position, raw in enumerate(request.recipients):
            certificate_id = str(uuid4())
            try:
                recipient = Recipient.model_validate(raw).model_dump(mode="json")
                status, error = "pending", None
            except ValidationError as exc:
                recipient = raw
                status = "failed"
                error = "; ".join(
                    f"{'.'.join(map(str, item['loc'])) or 'recipient'}: {item['msg']}"
                    for item in exc.errors(include_url=False, include_input=False)
                )
            rows.append((certificate_id, job_id, position, json.dumps(recipient), status, error))
        with connect(database) as db:
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    request.title,
                    request.course,
                    request.issuer,
                    request.issued_on.isoformat(),
                    datetime.now(UTC).isoformat(),
                    "queued",
                ),
            )
            db.executemany("INSERT INTO certificates VALUES (?, ?, ?, ?, ?, ?)", rows)
        background_tasks.add_task(process_job, database, output_dir, job_id)
        return {
            "id": job_id,
            "status": "queued",
            "total": len(rows),
            "status_url": f"/jobs/{job_id}",
        }

    @app.get("/jobs/{job_id}")
    def get_job(job_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        with connect(database) as db:
            db.execute("BEGIN")  # Read status, counters, and results from one snapshot.
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise HTTPException(404, "Job not found")
            job = dict(row)
            counts = dict(
                db.execute(
                    "SELECT status, COUNT(*) FROM certificates WHERE job_id = ? GROUP BY status",
                    (str(job_id),),
                ).fetchall()
            )
            rows = db.execute(
                "SELECT * FROM certificates WHERE job_id = ? ORDER BY position LIMIT ? OFFSET ?",
                (str(job_id), limit, offset),
            ).fetchall()
        completed, failed = counts.get("completed", 0), counts.get("failed", 0)
        total = sum(counts.values())
        job.update(
            total=total,
            completed=completed,
            failed=failed,
            processed=completed + failed,
            pending=counts.get("pending", 0),
            progress=round(100 * (completed + failed) / total, 2),
            offset=offset,
            limit=limit,
        )
        job["certificates"] = [
            {
                "id": row["id"],
                "position": row["position"],
                "recipient": json.loads(row["recipient"]),
                "status": row["status"],
                "error": row["error"],
                "download_url": (
                    f"/certificates/{row['id']}" if row["status"] == "completed" else None
                ),
            }
            for row in rows
        ]
        return job

    @app.get("/certificates/{certificate_id}")
    def download_certificate(certificate_id: UUID):
        with connect(database) as db:
            row = db.execute(
                "SELECT status FROM certificates WHERE id = ?", (str(certificate_id),)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Certificate not found")
        if row["status"] != "completed":
            raise HTTPException(409, "Certificate is not available")
        path = output_dir / f"{certificate_id}.html"
        if not path.is_file():
            raise HTTPException(404, "Certificate file not found")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            filename=f"certificate-{certificate_id}.html",
        )

    return app


app = create_app()
