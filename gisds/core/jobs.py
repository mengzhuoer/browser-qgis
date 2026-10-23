"""作业管理：耗时任务（尤其是 Processing 算法）的异步执行与状态查询。

为什么要作业化：Processing 算法可能要跑几十秒甚至几分钟，如果做成同步 HTTP 请求，
浏览器会一直等，而且没法展示进度。做法是：

1. ``POST /api/processing/run`` 立即返回 job_id；
2. 算法在 QGIS 专属线程里串行执行（与其它 QGIS 调用互斥，保证安全）；
3. 前端轮询 ``GET /api/processing/jobs/{id}`` 拿进度和日志。

状态字典由普通 Python 对象维护，轮询接口不经过 QGIS 线程，
所以即使算法正在跑，状态查询也是秒回。
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid
from typing import Any, Callable

from .executor import run_qgis


class JobManager:
    """轻量作业队列。"""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 创建

    def create(
        self,
        kind: str,
        title: str,
        meta: dict[str, Any] | None = None,
        owner: str = "",
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "title": title,
                "owner": owner,
                "status": "queued",
                "progress": 0.0,
                "message": "排队中",
                "logs": [],
                "outputs": {},
                "error": None,
                "meta": meta or {},
                "created_at": time.time(),
                "finished_at": None,
            }
            self._order.insert(0, job_id)
            # 只保留最近 100 条
            while len(self._order) > 100:
                old = self._order.pop()
                self._jobs.pop(old, None)
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit: int = 30, owner: str | None = None) -> list[dict[str, Any]]:
        """作业清单（新的在前）。

        ``owner`` 不为 None 时只返回**这个人自己的**作业 ——
        作业的 meta 里有图层引用、outputs 里有服务器上的绝对路径，
        别人的作业不该给谁看。
        """
        with self._lock:
            items = [dict(self._jobs[jid]) for jid in self._order if jid in self._jobs]
            if owner is not None:
                items = [j for j in items if (j.get("owner") or "") == owner]
            return items[:limit]

    def clear(self, owner: str | None = None) -> list[str]:
        """清掉作业记录，返回被清掉的 job_id 列表。

        ``owner`` 为 None 表示全清。只清记录，**不删输出文件** ——
        删文件是 API 层的事（那边知道文件在哪儿）。
        """
        with self._lock:
            gone: list[str] = []
            for jid in list(self._order):
                job = self._jobs.get(jid)
                if job is None:
                    self._order.remove(jid)
                    continue
                if owner is not None and (job.get("owner") or "") != owner:
                    continue
                gone.append(jid)
                self._jobs.pop(jid, None)
                self._order.remove(jid)
            return gone

    # ------------------------------------------------------------ 状态更新（线程安全）

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["logs"].append(line)
                if len(job["logs"]) > 500:
                    del job["logs"][:-500]

    def set_outputs(self, job_id: str, outputs: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["outputs"] = outputs

    # ------------------------------------------------------------ 执行

    async def start(
        self,
        job_id: str,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[str, Any], None] | None = None,
        **kwargs: Any,
    ) -> str:
        """把作业投到 QGIS 工作线程执行，立即返回。"""

        async def _runner() -> None:
            self.update(job_id, status="running", message="执行中", progress=0.05)
            try:
                result = await run_qgis(fn, *args, **kwargs)
                if on_done is not None:
                    outcome = on_done(job_id, result)
                    if inspect.isawaitable(outcome):
                        await outcome
                self.update(
                    job_id,
                    status="success",
                    progress=1.0,
                    message="完成",
                    finished_at=time.time(),
                )
            except Exception as exc:
                self.update(
                    job_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    message="失败",
                    finished_at=time.time(),
                )

        task = asyncio.create_task(_runner())
        with self._lock:
            self._tasks[job_id] = task
        return job_id

    def wait(self, job_id: str, timeout: float = 600.0) -> dict[str, Any] | None:
        """同步等待作业结束（给 CLI / 测试用）。"""
        task = self._tasks.get(job_id)
        if task is not None:
            try:
                asyncio.get_event_loop().run_until_complete(asyncio.wait_for(task, timeout))
            except Exception:
                pass
        return self.get(job_id)


MANAGER = JobManager()
