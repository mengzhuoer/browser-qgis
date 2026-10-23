"""就绪标志。

``/healthz`` 以前是「进程起来了就 200」。但服务的真正就绪要等 lifespan 跑完：
QGIS 初始化、Processing 装载、首个管理员建号、数据目录扫描 —— 这些没做完之前，
调用方（自检脚本、启动器 GUI、负载均衡）会以为服务已经好了。

自检就踩过这个坑：``/healthz`` 一返回 200，脚本立刻去查「管理员口令备忘写了没」，
而那会儿 lifespan 还在建管理员，备忘文件当然还不存在 —— 表现为**偶发**失败
（全量回归时机器忙就红、单独跑就绿），最难查的那种。

所以这里给一个显式标志。分三态而不是布尔，是为了区分「没开始」和「正在启动」：

* ``idle`` —— lifespan 还没跑（进程内直接用 FastAPI 实例、没走服务器时）。
  这时探针维持老行为，不影响既有用法。
* ``starting`` —— lifespan 正在跑，**/readyz** 返回 503，调用方自然就会等。
* ``ready`` —— 一切就绪。

**两个探针各管一件事**（别再让一个端点背两个语义）：

* ``/healthz`` —— **存活**：进程在就 200。给外部监控做心跳用。
  启动期也 200 —— 让它 503 会被配置不当的监控误判成「服务挂了」，反而触发重启循环。
* ``/readyz`` —— **就绪**：没准备好就 503。给「什么时候可以开始取数」的调用方用
  （自检脚本、启动器 GUI）。
"""

from __future__ import annotations

IDLE = "idle"
STARTING = "starting"
READY = "ready"

_state = IDLE


def mark_starting() -> None:
    """lifespan 开始时调用。"""
    global _state
    _state = STARTING


def mark_ready() -> None:
    """lifespan 全部跑完之后调用。"""
    global _state
    _state = READY


def mark_idle() -> None:
    """服务停止 / 从未启动。"""
    global _state
    _state = IDLE


def state() -> str:
    """当前状态：idle / starting / ready。"""
    return _state


def is_ready() -> bool:
    """服务是否已经可以正常对外服务。"""
    return _state == READY


def is_starting() -> bool:
    """服务是否正在启动（此时 /readyz 应该报 503）。"""
    return _state == STARTING
