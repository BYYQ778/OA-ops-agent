"""
定时任务调度模块
---------------
基于 APScheduler 实现巡检任务的定时调度：
- 支持手动启停巡检
- 支持动态调整巡检间隔（秒/分钟）
- 任务状态可查询
- 线程安全的状态管理
"""

import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from utils.logger import get_logger

logger = get_logger(__name__)


class InspectionScheduler:
    """
    巡检任务调度器（单例模式）。

    功能：
    - start(interval_seconds): 启动定时巡检
    - stop(): 停止巡检
    - adjust_interval(seconds): 运行时调整巡检间隔
    - is_running: 查询当前运行状态

    使用示例：
        scheduler = InspectionScheduler()
        scheduler.start(inspection_func, interval=30)  # 每30秒巡检一次
        scheduler.adjust_interval(60)                   # 调整为每60秒
        scheduler.stop()                                # 停止
    """

    def __init__(self):
        # 使用 BackgroundScheduler，不阻塞主线程
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={"misfire_grace_time": 15}  # 错过15秒内仍执行
        )
        self._job_id = "oa_inspection_job"
        self._running = False
        self._interval = 30  # 默认30秒巡检一次
        self._lock = threading.Lock()  # 保证线程安全

    @property
    def is_running(self) -> bool:
        """查询调度器是否正在运行"""
        return self._running

    @property
    def interval(self) -> int:
        """查询当前的巡检间隔（秒）"""
        return self._interval

    def start(self, task_func, interval: int = 30):
        """
        启动定时巡检。

        Args:
            task_func: 巡检任务函数（无参数、无返回值）
            interval: 巡检间隔，单位秒，默认30秒
        """
        with self._lock:
            if self._running:
                logger.warning("巡检调度器已在运行中，请勿重复启动")
                return False

            self._interval = interval

            # 如果调度器未运行则启动
            if not self._scheduler.running:
                self._scheduler.start()

            # 添加定时任务
            self._scheduler.add_job(
                func=task_func,
                trigger=IntervalTrigger(seconds=interval),
                id=self._job_id,
                name="OA巡检任务",
                replace_existing=True,  # 如果已存在则替换
                max_instances=1         # 同一时间最多1个实例运行
            )

            self._running = True
            logger.info(f"巡检调度器已启动，间隔: {interval}秒")
            return True

    def stop(self):
        """停止定时巡检"""
        with self._lock:
            if not self._running:
                logger.warning("巡检调度器未在运行")
                return False

            # 移除任务但保持调度器存活（便于再次启动）
            if self._scheduler.get_job(self._job_id):
                self._scheduler.remove_job(self._job_id)

            self._running = False
            logger.info("巡检调度器已停止")
            return True

    def adjust_interval(self, interval: int):
        """
        动态调整巡检间隔（无需停止重启）。

        Args:
            interval: 新的巡检间隔，单位秒
        """
        with self._lock:
            if not self._running:
                logger.warning("巡检调度器未运行，无法调整间隔")
                return False

            if interval < 5:
                logger.warning("巡检间隔不能小于5秒，已调整为5秒")
                interval = 5

            self._interval = interval

            # 重新调度任务（修改间隔）
            self._scheduler.reschedule_job(
                job_id=self._job_id,
                trigger=IntervalTrigger(seconds=interval)
            )

            logger.info(f"巡检间隔已调整为: {interval}秒")
            return True

    def shutdown(self):
        """彻底关闭调度器（程序退出时调用）"""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("巡检调度器已彻底关闭")
