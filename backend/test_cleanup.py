"""
test_cleanup.py — Unit Tests for /backend/cleanup.py
=====================================================
Validates:
  - Context manager: matrix deletion, fitz doc.close(), gc.collect() trigger.
  - Decorator: gc.collect() runs even when the decorated function raises.
  - Helper functions: release_matrix(), close_pdf_doc(), flush_memory().
  - Edge cases: None doc, empty matrix list, exception within context block.
"""

import gc
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch, call

# Make sure the backend directory is importable
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from cleanup import (
    enforce_stream_disposal,
    with_stream_disposal,
    release_matrix,
    close_pdf_doc,
    flush_memory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_doc():
    """Returns a MagicMock that mimics a fitz.Document."""
    doc = MagicMock()
    doc.close = MagicMock()
    return doc


def _make_mock_matrix():
    """Returns a simple object that stands in for a NumPy / OpenCV matrix."""
    return MagicMock(name="cv2_matrix")


# ---------------------------------------------------------------------------
# 1. Context Manager Tests
# ---------------------------------------------------------------------------

class TestEnforceStreamDisposalContextManager(unittest.TestCase):

    # 1a. Normal exit: doc.close() called, gc.collect() called
    @patch("cleanup.gc.collect", return_value=3)
    def test_doc_closed_on_normal_exit(self, mock_gc):
        doc = _make_mock_doc()
        with enforce_stream_disposal(doc=doc):
            pass
        doc.close.assert_called_once()
        mock_gc.assert_called_once()

    # 1b. Exception inside block: doc.close() still called (finally: guarantee)
    @patch("cleanup.gc.collect", return_value=0)
    def test_doc_closed_even_on_exception(self, mock_gc):
        doc = _make_mock_doc()
        with self.assertRaises(RuntimeError):
            with enforce_stream_disposal(doc=doc):
                raise RuntimeError("pipeline failure")
        doc.close.assert_called_once()

    # 1c. No doc provided: no AttributeError, gc still called
    @patch("cleanup.gc.collect", return_value=0)
    def test_no_doc_is_safe(self, mock_gc):
        with enforce_stream_disposal():
            pass
        mock_gc.assert_called_once()

    # 1d. Multiple positional matrices: block runs, gc called
    @patch("cleanup.gc.collect", return_value=5)
    def test_multiple_matrices_accepted(self, mock_gc):
        m1 = _make_mock_matrix()
        m2 = _make_mock_matrix()
        with enforce_stream_disposal(m1, m2):
            pass
        mock_gc.assert_called_once()

    # 1e. force_gc=False: gc.collect() must NOT be called
    @patch("cleanup.gc.collect")
    def test_no_gc_when_force_gc_false(self, mock_gc):
        doc = _make_mock_doc()
        with enforce_stream_disposal(doc=doc, force_gc=False):
            pass
        mock_gc.assert_not_called()

    # 1f. extra_objects list is cleaned up
    @patch("cleanup.gc.collect", return_value=1)
    def test_extra_objects_cleaned(self, mock_gc):
        extra = [object(), object()]
        with enforce_stream_disposal(extra_objects=extra):
            pass
        mock_gc.assert_called_once()

    # 1g. doc.close() raises: does NOT suppress the cleanup error (we log it
    #     but still call gc.collect())
    @patch("cleanup.gc.collect", return_value=0)
    def test_doc_close_exception_does_not_crash(self, mock_gc):
        doc = _make_mock_doc()
        doc.close.side_effect = OSError("file handle already closed")
        # Should not propagate the OSError
        with enforce_stream_disposal(doc=doc):
            pass
        mock_gc.assert_called_once()

    # 1h. Context manager yields (ensures body executes)
    def test_body_executes(self):
        executed = []
        with enforce_stream_disposal():
            executed.append(True)
        self.assertEqual(executed, [True])


# ---------------------------------------------------------------------------
# 2. Decorator Tests
# ---------------------------------------------------------------------------

class TestWithStreamDisposalDecorator(unittest.TestCase):

    # 2a. GC called after successful function
    @patch("cleanup.gc.collect", return_value=2)
    def test_gc_called_after_successful_fn(self, mock_gc):
        @with_stream_disposal()
        def do_work():
            return "ok"

        result = do_work()
        self.assertEqual(result, "ok")
        mock_gc.assert_called_once()

    # 2b. GC called even when function raises
    @patch("cleanup.gc.collect", return_value=0)
    def test_gc_called_even_on_exception(self, mock_gc):
        @with_stream_disposal()
        def do_work():
            raise ValueError("AI pipeline crash")

        with self.assertRaises(ValueError):
            do_work()
        mock_gc.assert_called_once()

    # 2c. force_gc=False: gc NOT called
    @patch("cleanup.gc.collect")
    def test_no_gc_when_force_gc_false(self, mock_gc):
        @with_stream_disposal(force_gc=False)
        def do_work():
            return 42

        do_work()
        mock_gc.assert_not_called()

    # 2d. Return value is preserved
    def test_return_value_preserved(self):
        @with_stream_disposal()
        def add(a, b):
            return a + b

        self.assertEqual(add(3, 4), 7)

    # 2e. functools.wraps preserves function metadata
    def test_wraps_preserves_metadata(self):
        @with_stream_disposal()
        def my_special_fn():
            """Docstring here."""
            pass

        self.assertEqual(my_special_fn.__name__, "my_special_fn")
        self.assertIn("Docstring", my_special_fn.__doc__)

    # 2f. Decorated function called multiple times: gc called each time
    @patch("cleanup.gc.collect", return_value=1)
    def test_gc_called_per_invocation(self, mock_gc):
        @with_stream_disposal()
        def process():
            pass

        process()
        process()
        process()
        self.assertEqual(mock_gc.call_count, 3)


# ---------------------------------------------------------------------------
# 3. Helper Function Tests
# ---------------------------------------------------------------------------

class TestHelperFunctions(unittest.TestCase):

    # 3a. release_matrix: runs without error, triggers gc
    @patch("cleanup.gc.collect", return_value=4)
    def test_release_matrix(self, mock_gc):
        mat = _make_mock_matrix()
        release_matrix(mat)
        mock_gc.assert_called_once()

    # 3b. close_pdf_doc: calls doc.close()
    def test_close_pdf_doc_calls_close(self):
        doc = _make_mock_doc()
        close_pdf_doc(doc)
        doc.close.assert_called_once()

    # 3c. close_pdf_doc with None: no-op, no AttributeError
    def test_close_pdf_doc_none_is_safe(self):
        close_pdf_doc(None)  # should not raise

    # 3d. close_pdf_doc: doc.close() raises OSError → warning logged, no crash
    def test_close_pdf_doc_handles_exception(self):
        doc = _make_mock_doc()
        doc.close.side_effect = OSError("already closed")
        close_pdf_doc(doc)  # should not propagate

    # 3e. flush_memory: calls gc.collect() and returns count
    @patch("cleanup.gc.collect", return_value=7)
    def test_flush_memory_returns_count(self, mock_gc):
        result = flush_memory()
        self.assertEqual(result, 7)
        mock_gc.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Integration-Style: Simulate a PDF Page Extraction Loop
# ---------------------------------------------------------------------------

class TestPDFExtractionSimulation(unittest.TestCase):

    @patch("cleanup.gc.collect", return_value=0)
    def test_pdf_page_loop_with_context_manager(self, mock_gc):
        """
        Simulate extracting 3 pages from a fitz doc and cleaning up.
        Verifies doc.close() is called once and gc.collect() once.
        """
        doc = _make_mock_doc()
        # Simulate 3 rendered page matrices
        pages = [_make_mock_matrix() for _ in range(3)]

        with enforce_stream_disposal(*pages, doc=doc):
            for i, page_matrix in enumerate(pages):
                # pretend to process
                _ = page_matrix

        doc.close.assert_called_once()
        mock_gc.assert_called_once()

    @patch("cleanup.gc.collect", return_value=0)
    def test_pdf_page_loop_exception_still_cleans(self, mock_gc):
        """
        If processing raises mid-loop, doc must still be closed.
        """
        doc = _make_mock_doc()
        pages = [_make_mock_matrix(), _make_mock_matrix()]

        with self.assertRaises(IndexError):
            with enforce_stream_disposal(*pages, doc=doc):
                raise IndexError("page render failed")

        doc.close.assert_called_once()
        mock_gc.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Integration-Style: Simulate Bulk Upload Batch
# ---------------------------------------------------------------------------

class TestBulkUploadBatchSimulation(unittest.TestCase):

    @patch("cleanup.gc.collect", return_value=0)
    def test_flush_called_per_file_iteration(self, mock_gc):
        """
        Simulate a bulk upload loop of 5 files, flushing memory after each.
        gc.collect() should be called 5 times.
        """
        for _ in range(5):
            flush_memory()

        self.assertEqual(mock_gc.call_count, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
