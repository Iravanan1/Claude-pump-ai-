"""
test_state_tracker.py — Unit Tests for /backend/state_tracker.py
=================================================================
Covers:
  1.  Schema:              init_state_db() creates the correct table + indexes.
  2.  calculate_file_hash: correct SHA-256, chunked reading, missing file, empty.
  3.  upsert_job:          inserts new row; does not overwrite COMPLETED rows.
  4.  get_job_status:      returns None for unknown hash; returns status string.
  5.  is_completed:        True only for COMPLETED, False for all other statuses.
  6.  mark_processing:     transitions status and increments attempts counter.
  7.  mark_completed:      transitions status and clears last_error.
  8.  mark_failed:         sets FAILED_MATH / FAILED_OCR and records error text.
  9.  reset_stuck_jobs:    PROCESSING → PENDING; leaves other statuses alone.
  10. get_all_jobs:        returns all rows; filters by status correctly.
  11. get_batch_stats:     aggregates counts per status correctly.
  12. JobStatus enum:      all_values() / failed_statuses() helpers.
  13. Full workflow:       PENDING → PROCESSING → COMPLETED end-to-end.
  14. Crash recovery:      multiple stuck jobs all reset in one call.
"""

import os
import sys
import sqlite3
import tempfile
import hashlib
import unittest

# ── Path setup ────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from state_tracker import (
    init_state_db,
    calculate_file_hash,
    get_job_status,
    is_completed,
    upsert_job,
    mark_processing,
    mark_completed,
    mark_failed,
    reset_stuck_jobs,
    get_all_jobs,
    get_batch_stats,
    JobStatus,
    DB_PATH as REAL_DB_PATH,
)


# =========================================================================
# Helpers
# =========================================================================

def _tmp_db() -> str:
    """Creates an isolated temp SQLite file; caller is responsible for cleanup."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _tmp_file(content: bytes = b"pumpai test file content") -> str:
    """Creates a temp file with known content and returns its path."""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _expected_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _insert_row(db: str, image_hash: str, file_name: str, status: str,
                attempts: int = 0, last_error: str = None) -> None:
    """Directly inserts a row into batch_status for test setup."""
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT OR REPLACE INTO batch_status
           (image_hash, file_name, status, attempts, last_error)
           VALUES (?, ?, ?, ?, ?)""",
        (image_hash, file_name, status, attempts, last_error)
    )
    conn.commit()
    conn.close()


# =========================================================================
# 1. Schema Tests
# =========================================================================

class TestInitStateDb(unittest.TestCase):

    def test_table_created(self):
        db = _tmp_db()
        try:
            init_state_db(db_path=db)
            conn = sqlite3.connect(db)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
            self.assertIn("batch_status", tables)
        finally:
            os.unlink(db)

    def test_expected_columns(self):
        db = _tmp_db()
        try:
            init_state_db(db_path=db)
            conn = sqlite3.connect(db)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(batch_status)").fetchall()]
            conn.close()
            for col in ("image_hash", "file_name", "status", "attempts", "last_error"):
                self.assertIn(col, cols)
        finally:
            os.unlink(db)

    def test_idempotent(self):
        db = _tmp_db()
        try:
            init_state_db(db_path=db)
            init_state_db(db_path=db)   # second call must not raise
        finally:
            os.unlink(db)

    def test_indexes_created(self):
        db = _tmp_db()
        try:
            init_state_db(db_path=db)
            conn = sqlite3.connect(db)
            indexes = [r[1] for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index'"
            ).fetchall()]
            conn.close()
            self.assertTrue(any("status" in idx for idx in indexes))
            self.assertTrue(any("filename" in idx.lower() for idx in indexes))
        finally:
            os.unlink(db)


# =========================================================================
# 2. calculate_file_hash Tests
# =========================================================================

class TestCalculateFileHash(unittest.TestCase):

    def test_known_content_hash(self):
        content = b"PumpAI register content"
        path = _tmp_file(content)
        try:
            result = calculate_file_hash(path)
            self.assertEqual(result, _expected_hash(content))
        finally:
            os.unlink(path)

    def test_hash_is_64_char_hex(self):
        path = _tmp_file(b"some bytes")
        try:
            result = calculate_file_hash(path)
            self.assertEqual(len(result), 64)
            self.assertTrue(all(c in "0123456789abcdef" for c in result))
        finally:
            os.unlink(path)

    def test_same_content_same_hash(self):
        content = b"identical content"
        p1 = _tmp_file(content)
        p2 = _tmp_file(content)
        try:
            self.assertEqual(calculate_file_hash(p1), calculate_file_hash(p2))
        finally:
            os.unlink(p1)
            os.unlink(p2)

    def test_different_content_different_hash(self):
        p1 = _tmp_file(b"content A")
        p2 = _tmp_file(b"content B")
        try:
            self.assertNotEqual(calculate_file_hash(p1), calculate_file_hash(p2))
        finally:
            os.unlink(p1)
            os.unlink(p2)

    def test_rename_does_not_change_hash(self):
        """Hash is content-based; renaming must not affect it."""
        content = b"register data"
        fd, original = tempfile.mkstemp(suffix="_original.jpg")
        os.close(fd)
        fd, renamed  = tempfile.mkstemp(suffix="_renamed.jpg")
        os.close(fd)
        try:
            with open(original, "wb") as f: f.write(content)
            with open(renamed,  "wb") as f: f.write(content)
            self.assertEqual(calculate_file_hash(original),
                             calculate_file_hash(renamed))
        finally:
            os.unlink(original)
            os.unlink(renamed)

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            calculate_file_hash("/does/not/exist.jpg")

    def test_empty_file_produces_known_hash(self):
        path = _tmp_file(b"")
        try:
            result = calculate_file_hash(path)
            # SHA-256 of empty bytes is well-known
            self.assertEqual(
                result,
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            )
        finally:
            os.unlink(path)


# =========================================================================
# 3. upsert_job Tests
# =========================================================================

class TestUpsertJob(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_inserts_new_row(self):
        upsert_job("abc123", "scan01.jpg", db_path=self.db)
        status = get_job_status("abc123", db_path=self.db)
        self.assertEqual(status, JobStatus.PENDING.value)

    def test_default_status_is_pending(self):
        upsert_job("hash1", "file.jpg", db_path=self.db)
        self.assertEqual(get_job_status("hash1", db_path=self.db), "PENDING")

    def test_updates_filename_if_not_completed(self):
        upsert_job("hash2", "old_name.jpg", db_path=self.db)
        upsert_job("hash2", "new_name.jpg", db_path=self.db)
        rows = get_all_jobs(db_path=self.db)
        self.assertEqual(rows[0]["file_name"], "new_name.jpg")

    def test_does_not_overwrite_completed_row(self):
        """A COMPLETED row must never be downgraded by upsert."""
        upsert_job("hash3", "done.jpg", db_path=self.db)
        mark_completed("hash3", db_path=self.db)
        # Try to upsert again with a different name
        upsert_job("hash3", "different_name.jpg", db_path=self.db)
        self.assertEqual(get_job_status("hash3", db_path=self.db), "COMPLETED")

    def test_idempotent_upsert(self):
        upsert_job("hash4", "file.jpg", db_path=self.db)
        upsert_job("hash4", "file.jpg", db_path=self.db)
        rows = get_all_jobs(db_path=self.db)
        self.assertEqual(len(rows), 1)


# =========================================================================
# 4. get_job_status Tests
# =========================================================================

class TestGetJobStatus(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_returns_none_for_unknown_hash(self):
        self.assertIsNone(get_job_status("unknown_hash", db_path=self.db))

    def test_returns_pending_after_upsert(self):
        upsert_job("h1", "f.jpg", db_path=self.db)
        self.assertEqual(get_job_status("h1", db_path=self.db), "PENDING")

    def test_returns_updated_status(self):
        upsert_job("h2", "f.jpg", db_path=self.db)
        mark_processing("h2", db_path=self.db)
        self.assertEqual(get_job_status("h2", db_path=self.db), "PROCESSING")


# =========================================================================
# 5. is_completed Tests
# =========================================================================

class TestIsCompleted(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_false_for_unknown_hash(self):
        self.assertFalse(is_completed("nonexistent", db_path=self.db))

    def test_false_for_pending(self):
        upsert_job("h1", "f.jpg", db_path=self.db)
        self.assertFalse(is_completed("h1", db_path=self.db))

    def test_false_for_processing(self):
        upsert_job("h2", "f.jpg", db_path=self.db)
        mark_processing("h2", db_path=self.db)
        self.assertFalse(is_completed("h2", db_path=self.db))

    def test_false_for_failed_math(self):
        upsert_job("h3", "f.jpg", db_path=self.db)
        mark_failed("h3", "math error", is_ocr=False, db_path=self.db)
        self.assertFalse(is_completed("h3", db_path=self.db))

    def test_false_for_failed_ocr(self):
        upsert_job("h4", "f.jpg", db_path=self.db)
        mark_failed("h4", "ocr error", is_ocr=True, db_path=self.db)
        self.assertFalse(is_completed("h4", db_path=self.db))

    def test_true_for_completed(self):
        upsert_job("h5", "f.jpg", db_path=self.db)
        mark_completed("h5", db_path=self.db)
        self.assertTrue(is_completed("h5", db_path=self.db))


# =========================================================================
# 6. mark_processing Tests
# =========================================================================

class TestMarkProcessing(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_status_becomes_processing(self):
        upsert_job("h1", "f.jpg", db_path=self.db)
        mark_processing("h1", db_path=self.db)
        self.assertEqual(get_job_status("h1", db_path=self.db), "PROCESSING")

    def test_attempts_incremented(self):
        upsert_job("h2", "f.jpg", db_path=self.db)
        mark_processing("h2", db_path=self.db)
        mark_processing("h2", db_path=self.db)   # second attempt
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT attempts FROM batch_status WHERE image_hash='h2'").fetchone()
        conn.close()
        self.assertEqual(row[0], 2)

    def test_last_error_cleared(self):
        _insert_row(self.db, "h3", "f.jpg", "FAILED_OCR", last_error="previous error")
        mark_processing("h3", db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash='h3'").fetchone()
        conn.close()
        self.assertIsNone(row[0])


# =========================================================================
# 7. mark_completed Tests
# =========================================================================

class TestMarkCompleted(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_status_becomes_completed(self):
        upsert_job("h1", "f.jpg", db_path=self.db)
        mark_completed("h1", db_path=self.db)
        self.assertEqual(get_job_status("h1", db_path=self.db), "COMPLETED")

    def test_last_error_cleared(self):
        _insert_row(self.db, "h2", "f.jpg", "FAILED_MATH", last_error="some math error")
        mark_completed("h2", db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash='h2'").fetchone()
        conn.close()
        self.assertIsNone(row[0])

    def test_completed_idempotent(self):
        upsert_job("h3", "f.jpg", db_path=self.db)
        mark_completed("h3", db_path=self.db)
        mark_completed("h3", db_path=self.db)   # second call must be safe
        self.assertEqual(get_job_status("h3", db_path=self.db), "COMPLETED")


# =========================================================================
# 8. mark_failed Tests
# =========================================================================

class TestMarkFailed(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_is_ocr_false_gives_failed_math(self):
        upsert_job("h1", "f.jpg", db_path=self.db)
        mark_failed("h1", "nozzle mismatch", is_ocr=False, db_path=self.db)
        self.assertEqual(get_job_status("h1", db_path=self.db), "FAILED_MATH")

    def test_is_ocr_true_gives_failed_ocr(self):
        upsert_job("h2", "f.jpg", db_path=self.db)
        mark_failed("h2", "gemini error", is_ocr=True, db_path=self.db)
        self.assertEqual(get_job_status("h2", db_path=self.db), "FAILED_OCR")

    def test_error_message_stored(self):
        upsert_job("h3", "f.jpg", db_path=self.db)
        mark_failed("h3", "specific error detail", is_ocr=False, db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash='h3'").fetchone()
        conn.close()
        self.assertIn("specific error detail", row[0])

    def test_long_error_truncated_gracefully(self):
        upsert_job("h4", "f.jpg", db_path=self.db)
        # mark_failed truncates at 1000 chars
        long_err = "x" * 5000
        mark_failed("h4", long_err, is_ocr=True, db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash='h4'").fetchone()
        conn.close()
        self.assertLessEqual(len(row[0]), 1000)


# =========================================================================
# 9. reset_stuck_jobs Tests
# =========================================================================

class TestResetStuckJobs(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_returns_zero_when_no_stuck_jobs(self):
        count = reset_stuck_jobs(db_path=self.db)
        self.assertEqual(count, 0)

    def test_resets_single_stuck_job(self):
        _insert_row(self.db, "h1", "f.jpg", "PROCESSING")
        count = reset_stuck_jobs(db_path=self.db)
        self.assertEqual(count, 1)
        self.assertEqual(get_job_status("h1", db_path=self.db), "PENDING")

    def test_resets_multiple_stuck_jobs(self):
        for i in range(5):
            _insert_row(self.db, f"hash_{i}", f"file_{i}.jpg", "PROCESSING")
        count = reset_stuck_jobs(db_path=self.db)
        self.assertEqual(count, 5)
        for i in range(5):
            self.assertEqual(get_job_status(f"hash_{i}", db_path=self.db), "PENDING")

    def test_leaves_completed_untouched(self):
        _insert_row(self.db, "done", "done.jpg", "COMPLETED")
        reset_stuck_jobs(db_path=self.db)
        self.assertEqual(get_job_status("done", db_path=self.db), "COMPLETED")

    def test_leaves_pending_untouched(self):
        _insert_row(self.db, "pend", "pend.jpg", "PENDING")
        reset_stuck_jobs(db_path=self.db)
        self.assertEqual(get_job_status("pend", db_path=self.db), "PENDING")

    def test_leaves_failed_math_untouched(self):
        _insert_row(self.db, "fm", "fm.jpg", "FAILED_MATH")
        reset_stuck_jobs(db_path=self.db)
        self.assertEqual(get_job_status("fm", db_path=self.db), "FAILED_MATH")

    def test_leaves_failed_ocr_untouched(self):
        _insert_row(self.db, "fo", "fo.jpg", "FAILED_OCR")
        reset_stuck_jobs(db_path=self.db)
        self.assertEqual(get_job_status("fo", db_path=self.db), "FAILED_OCR")

    def test_last_error_set_after_reset(self):
        _insert_row(self.db, "stuck", "stuck.jpg", "PROCESSING")
        reset_stuck_jobs(db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT last_error FROM batch_status WHERE image_hash='stuck'").fetchone()
        conn.close()
        self.assertIn("crash-recovery", row[0].lower())


# =========================================================================
# 10. get_all_jobs Tests
# =========================================================================

class TestGetAllJobs(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_empty_db_returns_empty_list(self):
        self.assertEqual(get_all_jobs(db_path=self.db), [])

    def test_returns_all_rows(self):
        for i in range(4):
            _insert_row(self.db, f"h{i}", f"f{i}.jpg", "PENDING")
        rows = get_all_jobs(db_path=self.db)
        self.assertEqual(len(rows), 4)

    def test_status_filter_pending(self):
        _insert_row(self.db, "h1", "f1.jpg", "PENDING")
        _insert_row(self.db, "h2", "f2.jpg", "COMPLETED")
        rows = get_all_jobs(status_filter="PENDING", db_path=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PENDING")

    def test_status_filter_completed(self):
        _insert_row(self.db, "h1", "f1.jpg", "PENDING")
        _insert_row(self.db, "h2", "f2.jpg", "COMPLETED")
        rows = get_all_jobs(status_filter="COMPLETED", db_path=self.db)
        self.assertEqual(len(rows), 1)

    def test_rows_are_dicts(self):
        _insert_row(self.db, "h1", "f1.jpg", "PENDING")
        rows = get_all_jobs(db_path=self.db)
        self.assertIsInstance(rows[0], dict)
        self.assertIn("image_hash", rows[0])
        self.assertIn("file_name",  rows[0])
        self.assertIn("status",     rows[0])


# =========================================================================
# 11. get_batch_stats Tests
# =========================================================================

class TestGetBatchStats(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_empty_db_all_zeros(self):
        stats = get_batch_stats(db_path=self.db)
        self.assertEqual(stats["total"], 0)
        for key in ("PENDING", "PROCESSING", "COMPLETED", "FAILED_MATH", "FAILED_OCR"):
            self.assertEqual(stats[key], 0)

    def test_counts_match_inserted_rows(self):
        _insert_row(self.db, "h1", "f1.jpg", "COMPLETED")
        _insert_row(self.db, "h2", "f2.jpg", "COMPLETED")
        _insert_row(self.db, "h3", "f3.jpg", "PENDING")
        _insert_row(self.db, "h4", "f4.jpg", "FAILED_OCR")
        stats = get_batch_stats(db_path=self.db)
        self.assertEqual(stats["COMPLETED"],  2)
        self.assertEqual(stats["PENDING"],    1)
        self.assertEqual(stats["FAILED_OCR"], 1)
        self.assertEqual(stats["total"],      4)

    def test_total_is_sum_of_all_statuses(self):
        for i, status in enumerate(["PENDING", "PROCESSING", "COMPLETED",
                                    "FAILED_MATH", "FAILED_OCR"]):
            _insert_row(self.db, f"h{i}", f"f{i}.jpg", status)
        stats = get_batch_stats(db_path=self.db)
        self.assertEqual(stats["total"], 5)


# =========================================================================
# 12. JobStatus Enum Tests
# =========================================================================

class TestJobStatusEnum(unittest.TestCase):

    def test_all_values_contains_six_statuses(self):
        vals = JobStatus.all_values()
        self.assertEqual(len(vals), 6)
        for expected in ("PENDING", "PROCESSING", "COMPLETED", "FAILED_MATH", "FAILED_OCR", "FAILED_PREPROCESSING"):
            self.assertIn(expected, vals)

    def test_failed_statuses_contains_three(self):
        fs = JobStatus.failed_statuses()
        self.assertIn("FAILED_MATH", fs)
        self.assertIn("FAILED_OCR", fs)
        self.assertIn("FAILED_PREPROCESSING", fs)
        self.assertNotIn("COMPLETED", fs)
        self.assertNotIn("PENDING",   fs)

    def test_str_value_matches(self):
        self.assertEqual(JobStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(str(JobStatus.PENDING),     "JobStatus.PENDING")


# =========================================================================
# 13. Full Workflow End-to-End
# =========================================================================

class TestFullWorkflow(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_pending_to_processing_to_completed(self):
        h = "workflow_hash_abc"
        # Register
        upsert_job(h, "day_01.jpg", db_path=self.db)
        self.assertEqual(get_job_status(h, db_path=self.db), "PENDING")
        self.assertFalse(is_completed(h, db_path=self.db))

        # Start processing
        mark_processing(h, db_path=self.db)
        self.assertEqual(get_job_status(h, db_path=self.db), "PROCESSING")
        self.assertFalse(is_completed(h, db_path=self.db))

        # Finish
        mark_completed(h, db_path=self.db)
        self.assertEqual(get_job_status(h, db_path=self.db), "COMPLETED")
        self.assertTrue(is_completed(h, db_path=self.db))

    def test_pending_to_processing_to_failed_ocr(self):
        h = "failed_ocr_hash"
        upsert_job(h, "day_02.jpg", db_path=self.db)
        mark_processing(h, db_path=self.db)
        mark_failed(h, "Gemini API unreachable", is_ocr=True, db_path=self.db)
        self.assertEqual(get_job_status(h, db_path=self.db), "FAILED_OCR")
        self.assertFalse(is_completed(h, db_path=self.db))

    def test_file_hash_drives_skip_check(self):
        content = b"register page data for skip test"
        path = _tmp_file(content)
        try:
            h = calculate_file_hash(path)
            upsert_job(h, os.path.basename(path), db_path=self.db)
            self.assertFalse(is_completed(h, db_path=self.db))

            mark_processing(h, db_path=self.db)
            mark_completed(h, db_path=self.db)
            self.assertTrue(is_completed(h, db_path=self.db))
        finally:
            os.unlink(path)


# =========================================================================
# 14. Crash Recovery — Multiple Stuck Jobs
# =========================================================================

class TestCrashRecovery(unittest.TestCase):

    def setUp(self):
        self.db = _tmp_db()
        init_state_db(db_path=self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_all_stuck_jobs_recovered(self):
        # Simulate 3 jobs in-flight when server crashed
        for i in range(3):
            _insert_row(self.db, f"crash_{i}", f"file_{i}.jpg", "PROCESSING", attempts=1)

        # Also add one COMPLETED and one FAILED_OCR — must not be touched
        _insert_row(self.db, "done", "done.jpg", "COMPLETED")
        _insert_row(self.db, "fail", "fail.jpg", "FAILED_OCR")

        recovered = reset_stuck_jobs(db_path=self.db)
        self.assertEqual(recovered, 3)

        # Check the three recovered jobs are now PENDING
        for i in range(3):
            self.assertEqual(get_job_status(f"crash_{i}", db_path=self.db), "PENDING")

        # Check others are untouched
        self.assertEqual(get_job_status("done", db_path=self.db), "COMPLETED")
        self.assertEqual(get_job_status("fail", db_path=self.db), "FAILED_OCR")

    def test_attempts_counter_preserved_after_recovery(self):
        """The attempt count must not be reset by crash recovery."""
        _insert_row(self.db, "retry", "retry.jpg", "PROCESSING", attempts=3)
        reset_stuck_jobs(db_path=self.db)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT attempts FROM batch_status WHERE image_hash='retry'").fetchone()
        conn.close()
        self.assertEqual(row[0], 3)   # preserved


if __name__ == "__main__":
    unittest.main(verbosity=2)
