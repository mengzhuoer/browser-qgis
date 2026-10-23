"""``python -m gisds.launcher`` 入口。

带 ``files`` 子参数时只开文件浏览器窗口：

    python -m gisds.launcher          # 启动器主窗口
    python -m gisds.launcher files    # 只开文件浏览器窗口
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "files":
        from .files import main

        raise SystemExit(main())
    from .app import main

    raise SystemExit(main())
