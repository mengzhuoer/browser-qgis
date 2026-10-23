"""示例插件：把插件 SDK 的每个能力都用一遍。

装插件有两种办法：

1. 把这个目录（连同 ``metadata.json``）拷到插件目录
   （默认 ``data/plugins/``，可以用 ``config.json`` 的 ``plugin_dir`` 改）；
2. 在网页上「插件 → 安装插件」上传本目录压成的 zip。

插件是**服务端代码**，装上去等于在数据服务器上放了一段任意代码 ——
所以只有管理员能装，而且装之前请自己审一遍。
"""

from __future__ import annotations

from typing import Any

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingProvider,
)


# --------------------------------------------------------------------- 算法


class FeatureCountAlgorithm(QgsProcessingAlgorithm):
    """数一数输入图层里有多少要素。

    注册之后它会出现在工具箱里，和 QGIS 自带的 440 个算法并列。
    """

    #: 算法名 —— **不含 provider 前缀**。
    #:
    #: QGIS 的算法 id 是 ``provider.id() + ":" + name()``，而这里的 provider
    #: id 已经是 ``gisds_hello``；``name()`` 再带一遍前缀，id 就成了
    #: ``gisds_hello:gisds_hello:featurecount``（多一层，不好看也不好记）。
    COUNT = "featurecount"
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"

    def createInstance(self):  # noqa: N802 - QGIS 的命名
        return FeatureCountAlgorithm()

    def name(self) -> str:
        return self.COUNT

    def displayName(self) -> str:  # noqa: N802 - QGIS 的命名
        return "要素计数（示例插件）"

    def group(self) -> str:
        return "Browser QGIS 示例插件"

    def groupId(self) -> str:  # noqa: N802 - QGIS 的命名
        return "gisds_hello"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS 的命名
        return "统计输入图层的要素数量，用来演示插件怎么往工具箱里加算法。"

    def initAlgorithm(self, config=None):  # noqa: N802 - QGIS 的命名
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "输入图层"))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.OUTPUT,
                "输出数量",
                QgsProcessingParameterNumber.Integer,
                1,
                False,
            )
        )

    def processAlgorithm(  # noqa: N802 - QGIS 的命名
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("输入图层无效")
        count = source.featureCount()
        feedback.pushInfo(f"要素数：{count}")
        return {self.OUTPUT: count}


class HelloProvider(QgsProcessingProvider):
    """这个插件贡献的算法都归它管。"""

    def id(self) -> str:
        return "gisds_hello"

    def name(self) -> str:
        return "Browser QGIS 示例插件"

    def longName(self) -> str:  # noqa: N802 - QGIS 的命名
        return "Browser QGIS 示例插件（示例）"

    def loadAlgorithms(self) -> None:  # noqa: N802 - QGIS 的命名
        self.addAlgorithm(FeatureCountAlgorithm())


# --------------------------------------------------------------------- 插件


class GisdPlugin:
    """插件主体。

    服务端会找到这个类，调 :meth:`init`（传进来一个 ``ctx``），
    卸载插件时调 :meth:`unload`。
    """

    def init(self, ctx) -> None:  # noqa: ANN001
        # 1) 往右栏加一个面板 —— 网页打开它时会 GET 这个接口
        ctx.add_panel(
            id="hello",
            title="示例插件",
            endpoint="/api/plugin/gisds_hello/panel",
            type="html",
        )
        # 2) 往菜单栏加一项（menu 可以是 file/edit/view/layer/tools/plugins）
        ctx.add_menu_item(
            menu="tools",
            label="示例插件：打个招呼",
            endpoint="/api/plugin/gisds_hello/hello",
            method="POST",
        )
        # 3) 注册自己的接口。handler 收到 (request, principal)
        ctx.add_route("/api/plugin/gisds_hello/hello", self._hello, methods=("GET", "POST"))
        ctx.add_route("/api/plugin/gisds_hello/panel", self._panel, methods=("GET",))
        # 4) 注册一个能从前端调用的动作
        ctx.add_action("hello", self._action)
        # 5) 往工具箱里加算法
        ctx.register_processing_provider(HelloProvider())
        ctx.log("示例插件已初始化")

    def unload(self) -> None:
        """插件被停用 / 卸载时调用。"""
        # 这里不用自己摘 provider —— 框架会替我们撤掉。
        pass

    # -- 接口实现 ------------------------------------------------------
    def _hello(self, request, principal) -> dict[str, Any]:  # noqa: ANN001
        who = getattr(principal, "display_name", "") or getattr(principal, "username", "")
        return {"message": f"你好，{who or '匿名访客'}！", "plugin": "gisds_hello"}

    def _panel(self, request, principal) -> dict[str, Any]:  # noqa: ANN001
        return {
            "html": (
                '<div class="sec">'
                '<div class="row">这个面板由 <b>示例插件</b> 提供。</div>'
                '<div class="row" style="margin-top:6px">'
                "面板内容就是插件自己的接口返回的 HTML —— "
                "插件也可以返回 columns / rows 渲染成表格。</div>"
                '<div class="row" style="margin-top:6px">'
                "插件还注册了一个 Processing 算法：<b>要素计数（示例插件）</b>，"
                "在工具箱的「Browser QGIS 示例插件」分组里。</div>"
                "</div>"
            )
        }

    def _action(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "世界")
        return {"greeting": f"你好，{name}！"}
