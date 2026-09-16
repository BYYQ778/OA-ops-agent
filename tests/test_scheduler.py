"""Exercise the real scheduler lifecycle without waiting for scheduled execution."""

from utils.scheduler import InspectionScheduler


def test_start_adjust_stop_and_shutdown():
    scheduler = InspectionScheduler()
    try:
        assert not scheduler.is_running
        assert scheduler.stop() is False
        assert scheduler.adjust_interval(60) is False
        assert scheduler.start(lambda: None, interval=600) is True
        assert scheduler.is_running
        assert scheduler.interval == 600
        assert scheduler.start(lambda: None, interval=1200) is False
        assert scheduler.adjust_interval(1) is True
        assert scheduler.interval == 5
        assert scheduler.stop() is True
        assert not scheduler.is_running
        assert scheduler._scheduler.get_job(scheduler._job_id) is None
    finally:
        scheduler.shutdown()
    assert not scheduler._scheduler.running


def test_shutdown_running_scheduler():
    scheduler = InspectionScheduler()
    scheduler.start(lambda: None, interval=600)
    scheduler.shutdown()
    assert not scheduler.is_running
    scheduler.shutdown()
