"""Tk 的两条硬约束（都是踩出来的）。

**一、Tk 只能在主线程动。**
后台线程里 insert / pack / destroy 控件，轻则偶发地不生效（控件时有时无），
重则整个程序崩掉（0xC0000005）。

**二、Tk 根窗口要留一份强引用。**
Tk 对象几乎总在引用环里（root ↔ Tk 回调 ↔ 持有它的那个对象），
循环 GC 随时可能把它收走 —— 而 GC 是**哪个线程分配得够多、就在哪个线程跑**。
要是正好在后台线程里被回收，Tcl 解释器就会在**错误的线程**里被删除：

    Tcl_AsyncDelete: async handler deleted by the wrong thread

然后进程直接 abort（退出码 0x80000003），什么都不打印。

这个坑很难查：它跟业务代码毫无关系，只在「后台线程 + 循环 GC + 引用环」
三者凑齐时才发作，而且一发作就是整个进程没了。

所以：建过的根窗口都登记在这里，永远不会被循环 GC 回收；
窗口该关的时候照常调 ``destroy()`` —— 那会把 Tcl 侧清干净，
解释器则留到进程退出时由主线程统一收尾。
"""

from __future__ import annotations

_ROOTS: list = []


def keep_root(root):
    """登记一个 Tk 根窗口（防止它被循环 GC 在后台线程里收走），返回它本身。"""
    if root is not None and not any(r is root for r in _ROOTS):
        _ROOTS.append(root)
    return root
