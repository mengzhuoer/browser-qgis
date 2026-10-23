"""统一的异常类型，API 层据此映射 HTTP 状态码。"""

from __future__ import annotations


class GisdsError(Exception):
    """所有业务异常的基类。"""

    status_code = 400
    code = "gisds_error"

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class NotFoundError(GisdsError):
    status_code = 404
    code = "not_found"


class InvalidRequestError(GisdsError):
    status_code = 422
    code = "invalid_request"


class DataError(GisdsError):
    """数据本身有问题（网格不一致、坐标系缺失、文件损坏等）。"""

    status_code = 422
    code = "data_error"


class QgisRuntimeError(GisdsError):
    status_code = 500
    code = "qgis_runtime_error"
