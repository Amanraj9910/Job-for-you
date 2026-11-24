"""TG2: SQLite store — migrations, idempotent upserts, status changes, append-only events."""
import sqlite3

import pytest

from jobhunt import db
from jobhunt.models import Channel, Job


def make_job(**kw) -> Job:
    base = {"source": "greenhouse", "source_job_id": "123", "title": "AI Engineer",
            "company": "Acme Labs Pvt Ltd",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/123", "location": "Bengaluru, India",
            "jd_text": "Build RAG systems on Azure.", "channel": Channel.AUTO}
    base.update(kw)
    return Job(**base)


@pytest.fixture
def conn(tmp_home):
    c = db.connect()
    yield c
    c.close()


def test_migrations_create_all_tables_and_are_rerunnable(conn, tmp_home):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"jobs", "scores", "companies", "events", "source_health", "watchlist", "probe_state",
                "schema_version"}
    assert expected <= names
    assert not {"documents", "applications", "batches"} & names      # no apply machinery in this project
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c2 = db.connect(tmp_home.db_path)  # second connect = no-op migrate
    assert db.migrate(c2) == len(db.MIGRATIONS)
    c2.close()


def test_upsert_is_idempotent(conn):
    job = make_job()
    jid = db.upsert_job(conn, job)
    assert jid == job.job_id
    first = db.get_job(conn, jid)
    assert db.upsert_job(conn, job) == jid
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    again = db.get_job(conn, jid)
    assert again["first_seen"] == first["first_seen"]
    assert again["discovered_via"] == ["greenhouse"]
    assert again["company_key"] == "acme"
    assert again["status"] == "new"


def test_upsert_updates_mutable_fields_and_keeps_known_values(conn):
    jid = db.upsert_job(conn, make_job(salary_min=1_000_000, salary_max=1_500_000, salary_currency="INR"))
    db.upsert_job(conn, make_job(title="Senior AI Engineer", jd_text=""))
    j = db.get_job(conn, jid)
    assert j["title"] == "Senior AI Engineer"
    assert j["salary_min"] == 1_000_000  # missing on refresh -> kept
    assert j["jd_text"] == "Build RAG systems on Azure."


def test_discovered_via_merges_sources(conn):
    jid = db.upsert_job(conn, make_job())
    conn.execute("UPDATE jobs SET discovered_via = ? WHERE job_id = ?", ('["jobspy_linkedin"]', jid))
    db.upsert_job(conn, make_job())
    db.upsert_job(conn, make_job())
    assert db.get_job(conn, jid)["discovered_via"] == ["jobspy_linkedin", "greenhouse"]


def test_canonical_job_id_kept_when_not_given(conn):
    jid = db.upsert_job(conn, make_job(), canonical_job_id="canon1")
    db.upsert_job(conn, make_job())
    assert db.get_job(conn, jid)["canonical_job_id"] == "canon1"


def test_status_and_notes_survive_reupsert(conn):
    jid = db.upsert_job(conn, make_job())
    db.set_status(conn, jid, "shortlisted", "claude")
    db.set_notes(conn, jid, "ask about hybrid", "report")
    db.upsert_job(conn, make_job(title="AI Engineer II"))
    j = db.get_job(conn, jid)
    assert (j["status"], j["notes"], j["status_updated_by"]) == ("shortlisted", "ask about hybrid", "claude")


def test_set_status_validates_and_logs(conn):
    jid = db.upsert_job(conn, make_job())
    with pytest.raises(ValueError):
        db.set_status(conn, jid, "hired!", "cli")
    with pytest.raises(KeyError):
        db.set_status(conn, "nope", "closed", "cli")
    assert db.set_status(conn, jid, "closed", "cli", note="posting removed") is True
    assert db.set_status(conn, jid, "closed", "cli") is False  # no-op, no event
    ev = db.list_events(conn, job_id=jid, action="status_changed")
    assert len(ev) == 1
    assert ev[0]["actor"] == "cli"
    assert ev[0]["detail_json"] == {"from": "new", "to": "closed", "note": "posting removed"}


def test_user_editable_is_subset_and_has_no_apply_queue_states():
    assert set(db.USER_EDITABLE) <= set(db.TRACKER_STATUSES)
    assert not {"approved", "queued", "applied_manually"} & set(db.TRACKER_STATUSES)


def test_events_are_append_only(conn):
    eid = db.log_event(conn, "claude", "note", job_id=None, x=1)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET actor = 'evil' WHERE id = ?", (eid,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events WHERE id = ?", (eid,))
    assert db.list_events(conn)[0]["detail_json"] == {"x": 1}


def test_tx_rolls_back_nested_writes(conn):
    jid = db.upsert_job(conn, make_job())
    with pytest.raises(RuntimeError), db.tx(conn):
        db.set_status(conn, jid, "skipped", "cli")
        raise RuntimeError("boom")
    assert db.get_job(conn, jid)["status"] == "new"
    assert db.list_events(conn) == []


def test_list_jobs_filters(conn):
    a = db.upsert_job(conn, make_job())
    b = db.upsert_job(conn, make_job(source="lever", source_job_id="9", channel=Channel.MANUAL_OTHER))
    db.set_status(conn, b, "skipped", "cli")
    assert [j["job_id"] for j in db.list_jobs(conn, source="lever")] == [b]
    assert [j["job_id"] for j in db.list_jobs(conn, status=["new", "review"])] == [a]
    assert len(db.list_jobs(conn, channel=None)) == 2
    with pytest.raises(ValueError):
        db.list_jobs(conn, jd_text="x")


def test_source_health_and_status_counts(conn):
    a = db.upsert_job(conn, make_job())
    db.upsert_job(conn, make_job(source="lever", source_job_id="9"))
    db.add_score(conn, a, 85, "apply", heuristic=70, flags=["x"])
    db.record_source_health(conn, "run1", "greenhouse", True, 1)
    db.record_source_health(conn, "run1", "lever", False, 0, error="HTTP 404")
    rows = conn.execute("SELECT source, ok, error FROM source_health ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("greenhouse", 1, None), ("lever", 0, "HTTP 404")]
    c = db.status_counts(conn)
    assert c["total"] == 2
    assert c["by_status"] == {"new": 2}
    assert c["by_source"] == {"greenhouse": 1, "lever": 1}
    assert c["by_band"] == {"apply": 1, "unscored": 1}


# ---------------------------------------------------------------- review regressions

def test_empty_jd_does_not_get_a_shared_or_stale_hash(conn):
    jid = db.upsert_job(conn, make_job(jd_text="Build RAG on Azure"))
    good = db.get_job(conn, jid)["jd_hash"]
    assert good
    db.upsert_job(conn, make_job(jd_text=""))  # refresh without a JD keeps text AND its hash
    j = db.get_job(conn, jid)
    assert (j["jd_text"], j["jd_hash"]) == ("Build RAG on Azure", good)
    other = db.upsert_job(conn, make_job(source_job_id="456", jd_text=""))
    blank = db.upsert_job(conn, make_job(source_job_id="789", jd_text="   "))
    assert db.get_job(conn, other)["jd_hash"] is None and db.get_job(conn, blank)["jd_hash"] is None


def test_empty_field_on_refresh_keeps_old_value(conn):
    jid = db.upsert_job(conn, make_job(location="Bengaluru", department="AI"))
    db.upsert_job(conn, make_job(location="", department=""))
    j = db.get_job(conn, jid)
    assert (j["location"], j["department"]) == ("Bengaluru", "AI")


def test_events_cannot_be_replaced(conn):
    eid = db.log_event(conn, "claude", "note", x=1)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("INSERT OR REPLACE INTO events(id, ts, actor, action, detail_json) VALUES (?,?,?,?,?)",
                     (eid, "t", "evil", "r", "{}"))
    assert db.list_events(conn)[0]["actor"] == "claude"


def test_concurrent_connect_to_fresh_db(tmp_path):
    import threading

    def worker(path, start, errors):
        try:
            start.wait()
            db.connect(path).close()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    for i in range(5):
        path = tmp_path / f"fresh{i}.db"
        errors: list[BaseException] = []
        start = threading.Barrier(4)
        ts = [threading.Thread(target=worker, args=(path, start, errors)) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert errors == []
        c = db.connect(path)
        assert db.schema_version(c) == len(db.MIGRATIONS)
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        c.close()
