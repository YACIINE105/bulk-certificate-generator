from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import connect


@pytest.fixture
def payload():
    return {
        "title": "Certificate of Completion",
        "course": "Python Basics",
        "issuer": "Aereo Academy",
        "issued_on": "2026-10-07",
        "recipients": [
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"},
        ],
    }


@pytest.fixture
def client(tmp_path):
    with TestClient(main.create_app(tmp_path)) as client:
        yield client


def submit(client, payload):
    response = client.post("/jobs", json=payload)
    assert response.status_code == 202
    return response.json()


def test_create_generate_and_download(client, payload):
    accepted = submit(client, payload)
    assert accepted["status"] == "queued"
    assert accepted["total"] == 2
    job = client.get(accepted["status_url"]).json()
    assert job["status"] == "completed"
    assert (job["total"], job["completed"], job["failed"], job["pending"]) == (2, 2, 0, 0)
    assert job["progress"] == 100
    for certificate, recipient in zip(job["certificates"], payload["recipients"]):
        response = client.get(certificate["download_url"])
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "attachment" in response.headers["content-disposition"]
        for value in [
            *recipient.values(),
            payload["course"],
            payload["title"],
            payload["issuer"],
            payload["issued_on"],
            certificate["id"],
        ]:
            assert value in response.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "  "),
        ("course", ""),
        ("issuer", ""),
        ("issued_on", "not-a-date"),
        ("recipients", []),
        ("recipients", "Alice"),
    ],
)
def test_invalid_batch_rejected(client, payload, field, value):
    payload[field] = value
    assert client.post("/jobs", json=payload).status_code == 422


def test_missing_batch_field(client, payload):
    del payload["course"]
    assert client.post("/jobs", json=payload).status_code == 422


@pytest.mark.parametrize(
    "invalid",
    [
        {"name": "", "email": "x@example.com"},
        {"name": "Bad email", "email": "not-an-email"},
        {"name": "No email"},
        None,
        "invalid",
        {"name": 123, "email": "x@example.com"},
    ],
)
def test_invalid_recipient_does_not_block_valid_ones(client, payload, invalid):
    payload["recipients"].insert(1, invalid)
    accepted = submit(client, payload)
    job = client.get(accepted["status_url"]).json()
    assert job["status"] == "partial_failure"
    assert (job["completed"], job["failed"], job["processed"]) == (2, 1, 3)
    failed = job["certificates"][1]
    assert failed["error"]
    assert failed["download_url"] is None
    assert client.get(f"/certificates/{failed['id']}").status_code == 409


def test_all_recipients_invalid(client, payload):
    payload["recipients"] = [None, {}]
    job = client.get(submit(client, payload)["status_url"]).json()
    assert (job["status"], job["completed"], job["failed"], job["progress"]) == (
        "failed",
        0,
        2,
        100,
    )


def test_generation_failure_is_isolated(client, payload, monkeypatch):
    original = main.generate_certificate

    def fail_one(job, recipient, certificate_id, output):
        if recipient["name"] == "Alice":
            raise OSError("Simulated generation failure")
        original(job, recipient, certificate_id, output)

    monkeypatch.setattr(main, "generate_certificate", fail_one)
    job = client.get(submit(client, payload)["status_url"]).json()
    assert (job["status"], job["completed"], job["failed"]) == ("partial_failure", 1, 1)
    assert job["certificates"][0]["error"] == "Certificate generation failed"
    assert client.get(job["certificates"][1]["download_url"]).status_code == 200


def test_progress_while_background_task_runs(client, payload, tmp_path, monkeypatch):
    started, release = Event(), Event()
    original = main.generate_certificate

    def pause_second(job, recipient, certificate_id, output):
        if recipient["name"] == "Bob":
            started.set()
            assert release.wait(10)
        original(job, recipient, certificate_id, output)

    monkeypatch.setattr(main, "generate_certificate", pause_second)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/jobs", json=payload)
        try:
            assert started.wait(10)
            with connect(tmp_path / "certificates.sqlite3") as db:
                job_id = db.execute("SELECT id FROM jobs").fetchone()["id"]
            job = client.get(f"/jobs/{job_id}").json()
            assert (job["status"], job["completed"], job["pending"], job["progress"]) == (
                "processing",
                1,
                1,
                50,
            )
            pending = job["certificates"][1]
            assert client.get(f"/certificates/{pending['id']}").status_code == 409
        finally:
            release.set()
        assert future.result().status_code == 202
    assert client.get(f"/jobs/{job_id}").json()["status"] == "completed"


def test_pagination(client, payload):
    url = submit(client, payload)["status_url"]
    page = client.get(url, params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 2
    assert len(page["certificates"]) == 1
    assert page["certificates"][0]["recipient"]["name"] == "Bob"
    assert client.get(url, params={"offset": 10}).json()["certificates"] == []
    assert client.get(url, params={"limit": 1001}).status_code == 422
    assert client.get(url, params={"offset": -1}).status_code == 422


def test_unknown_and_malformed_ids(client):
    for prefix in ["/jobs/", "/certificates/"]:
        assert client.get(prefix + str(uuid4())).status_code == 404
        assert client.get(prefix + "invalid-id").status_code == 422


def test_html_escaping_and_unicode(client, payload):
    payload["recipients"] = [
        {
            "name": '<script>alert("x")</script> \u064a\u0627\u0633\u064a\u0646',
            "email": "alice@example.com",
        }
    ]
    payload["course"] = "Python & APIs"
    job = client.get(submit(client, payload)["status_url"]).json()
    html = client.get(job["certificates"][0]["download_url"]).text
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Python &amp; APIs" in html
    assert "\u064a\u0627\u0633\u064a\u0646" in html


def test_whitespace_normalization(client, payload):
    payload["recipients"][0]["name"] = "  Alice  "
    payload["title"] = "  Certificate  "
    job = client.get(submit(client, payload)["status_url"]).json()
    assert job["title"] == "Certificate"
    assert job["certificates"][0]["recipient"]["name"] == "Alice"


def test_completed_jobs_survive_restart(tmp_path, payload):
    with TestClient(main.create_app(tmp_path)) as first:
        url = submit(first, payload)["status_url"]
    with TestClient(main.create_app(tmp_path)) as second:
        job = second.get(url).json()
        assert job["status"] == "completed"
        assert second.get(job["certificates"][0]["download_url"]).status_code == 200


def test_restart_marks_unfinished_work_interrupted(tmp_path, payload, monkeypatch):
    monkeypatch.setattr(main.BackgroundTasks, "add_task", lambda *args, **kwargs: None)
    with TestClient(main.create_app(tmp_path)) as first:
        url = submit(first, payload)["status_url"]
        assert first.get(url).json()["status"] == "queued"
    with TestClient(main.create_app(tmp_path)) as second:
        job = second.get(url).json()
        assert (job["status"], job["failed"], job["pending"]) == ("interrupted", 2, 0)
        assert all("Server stopped" in row["error"] for row in job["certificates"])


def test_missing_certificate_file_returns_404(client, payload, tmp_path):
    job = client.get(submit(client, payload)["status_url"]).json()
    certificate = job["certificates"][0]
    (tmp_path / "certificates" / f"{certificate['id']}.html").unlink()
    assert client.get(certificate["download_url"]).status_code == 404


def test_bulk_request(client, payload):
    payload["recipients"] = [
        {"name": f"Recipient {index}", "email": f"person{index}@example.com"}
        for index in range(250)
    ]
    job = client.get(submit(client, payload)["status_url"]).json()
    assert (job["total"], job["completed"], job["failed"]) == (250, 250, 0)
    assert len(job["certificates"]) == 100


def test_bulk_limit(client, payload):
    payload["recipients"] = [payload["recipients"][0]] * 10001
    assert client.post("/jobs", json=payload).status_code == 422
