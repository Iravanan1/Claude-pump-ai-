"""
cleanup.py — Memory Optimization & Garbage Collection Utility
=============================================================
Provides a context manager (`enforce_stream_disposal`) and a matching
decorator (`@with_stream_disposal`) that:

  1. Explicitly delete OpenCV matrix variables after use.
  2. Call `doc.close()` inside a strict `finally:` block for fitz PDF documents.
  3. Force-invoke `gc.collect()` at the end of every upload iteration.

Usage — as a context manager:
    with enforce_stream_disposal(matrix=img, doc=pdf_doc):
        # work with img / pdf_doc
    # img and pdf_doc are closed / deleted here; gc.collect() is called.

Usage — as a decorator:
    @with_stream_disposal()
    def my_processing_fn(img, doc):
        ...
"""

import gc
import logging
import functools
from contextlib import contextmanager
from typing import Generator

# Use the project-wide structured logger when available; fall back to stdlib.
try:
    from logger import logger  # type: ignore
except ImportError:
    logger = logging.getLogger("cleanup")

# ---------------------------------------------------------------------------
# Context Manager
# ---------------------------------------------------------------------------

@contextmanager
def enforce_stream_disposal(
    *matrices,
    doc=None,
    extra_objects=None,
    force_gc: bool = True,
) -> Generator[None, None, None]:
    """
    Context manager that guarantees resource cleanup for OpenCV matrices and
    fitz PDF document handles after a processing block exits (normally or via
    an exception).

    Args:
        *matrices:      Any number of NumPy / OpenCV matrix objects to `del`
                        after the block.
        doc:            An open fitz.Document (or any object with a `.close()`
                        method).  Its `.close()` is called inside `finally:`.
        extra_objects:  Optional list of additional arbitrary objects to `del`.
        force_gc:       When True (default), calls `gc.collect()` after all
                        cleanup steps.

    Example::
        with enforce_stream_disposal(img, gray, doc=pdf_doc):
            result = process(img, gray)
    """
    try:
        yield
    finally:
        # --- 1. Release fitz document handle --------------------------------
        if doc is not None:
            try:
                doc.close()
                logger.debug("cleanup: fitz doc closed successfully.")
            except Exception as close_err:
                logger.warning(f"cleanup: error closing fitz doc — {close_err}")

        # --- 2. Delete OpenCV / NumPy matrices --------------------------------
        for mat in matrices:
            try:
                del mat
                logger.debug("cleanup: matrix reference released.")
            except Exception as del_err:
                logger.debug(f"cleanup: could not delete matrix ref — {del_err}")

        # --- 3. Delete extra arbitrary objects --------------------------------
        if extra_objects:
            for obj in extra_objects:
                try:
                    del obj
                except Exception:
                    pass

        # --- 4. Force CPython garbage collector --------------------------------
        if force_gc:
            collected = gc.collect()
            logger.debug(f"cleanup: gc.collect() freed {collected} objects.")


# ---------------------------------------------------------------------------
# Decorator (wraps a function, running cleanup after each call)
# ---------------------------------------------------------------------------

def with_stream_disposal(force_gc: bool = True):
    """
    Decorator factory that wraps a function and calls `gc.collect()` after
    every invocation.

    For finer-grained matrix / doc cleanup, use the context manager inside the
    function body itself.  This decorator ensures the GC sweep happens even
    when the caller forgets.

    Args:
        force_gc: When True (default), call `gc.collect()` in the `finally`
                  block after every invocation of the decorated function.

    Example::
        @with_stream_disposal()
        def process_page(img):
            ...  # heavy OpenCV work
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            finally:
                if force_gc:
                    collected = gc.collect()
                    logger.debug(
                        f"cleanup[@{func.__name__}]: gc.collect() freed "
                        f"{collected} objects."
                    )
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Explicit Helpers (for callers that cannot use with/decorator syntax)
# ---------------------------------------------------------------------------

def release_matrix(matrix) -> None:
    """
    Explicitly delete an OpenCV / NumPy matrix and trigger a GC pass.

    Call this right after a matrix exits the processing pipeline (e.g., after
    OpenCV thresholding has completed and the result has been saved).
    """
    try:
        del matrix
    except Exception:
        pass
    gc.collect()
    logger.debug("cleanup: explicit matrix released and gc.collect() invoked.")


def close_pdf_doc(doc) -> None:
    """
    Safely close a fitz PDF document.  No-op if `doc` is None.
    """
    if doc is None:
        return
    try:
        doc.close()
        logger.debug("cleanup: fitz document closed via close_pdf_doc().")
    except Exception as e:
        logger.warning(f"cleanup: failed to close fitz document — {e}")


def flush_memory() -> int:
    """
    Unconditionally run `gc.collect()` and return the number of freed objects.
    Use at the end of a bulk upload batch loop.
    """
    collected = gc.collect()
    logger.debug(f"cleanup: flush_memory() freed {collected} objects.")
    return collected
