"""tests/test_job_log_level.py — the job log window must not drop INFO lines.

Bug: ``_run_job`` attached a handler at INFO but never lowered the root
logger's level, which defaults to WARNING. Every ``[AgnesChat] HTTP 503,
retry 1/3 in ...`` line vanished, so the UI showed a silently stalled job
while the retry loop burned minutes (see ``comic_out/1f61fb46a59a``).
"""

import logging

from web import server


def test_job_log_window_captures_info_records():
    """INFO records must reach the job buffer while a job runs."""
    root = logging.getLogger()
    job = {"log": []}
    handler = server._JobLogHandler(job)
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        with server._job_log_level():
            logging.getLogger("core.pipelines.creative_comic").info("retry 1/3 in 4.0s")
    finally:
        root.removeHandler(handler)

    assert "retry 1/3 in 4.0s" in job["log"]


def test_job_log_window_drops_debug_records():
    """Lowering to INFO must not flood the buffer with DEBUG chatter."""
    root = logging.getLogger()
    job = {"log": []}
    handler = server._JobLogHandler(job)
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        with server._job_log_level():
            logging.getLogger("core.pipelines.creative_comic").debug("noise")
    finally:
        root.removeHandler(handler)

    assert job["log"] == []


def test_job_log_level_restores_root_level():
    """The root level must be put back, or the whole process turns chatty."""
    root = logging.getLogger()
    previous = root.level
    with server._job_log_level():
        assert root.level == logging.INFO
    assert root.level == previous


def test_job_log_level_nested_jobs_keep_info_until_last_exit():
    """Concurrent jobs must not restore the level to WARNING early."""
    root = logging.getLogger()
    previous = root.level
    with server._job_log_level():
        with server._job_log_level():
            assert root.level == logging.INFO
        assert root.level == logging.INFO
    assert root.level == previous
