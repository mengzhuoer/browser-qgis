"""数据集注册中心。

**目录即注册**：把符合约定的文件夹丢进 ``data_root``，服务就能看见它。

约定::

    <data_root>/
      <dataset_id>/                 # 一个子目录 = 一个数据集
        metadata.json               # 可选，描述名称/类别/年份等
        *.tif *.tiff *.img *.vrt    # 栅格图层
        *.geojson *.gpkg *.shp      # 矢量图层

``metadata.json`` 示例::

    {
      "name": "平朔矿区",
      "description": "CLCD 土地利用 5 期 + 驱动因子",
      "layers": {
        "landuse_2020": {
          "file": "landuse_2020.tif",
          "kind": "categorical",
          "year": 2020,
          "classes": {"1": "耕地", "2": "林地", "5": "裸地"}
        },
        "dem": {"file": "dem.tif", "kind": "continuous"}
      },
      "vectors": {
        "aoi": {"file": "aoi.geojson", "title": "研究区范围"}
      }
    }

没有 ``metadata.json`` 也能工作：图层 id 取文件名，类型自动推断。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._blocks import block_to_numpy, unique_values
from .errors import NotFoundError

RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".vrt", ".ntf"}
VECTOR_EXTENSIONS = {".geojson", ".gpkg", ".shp", ".gml", ".kml", ".json", ".sqlite"}

# 文件名里出现这些词，倾向于判定为离散型（分类）图层
_CATEGORICAL_HINTS = {
    "landuse",
    "land_use",
    "landcover",
    "cover",
    "class",
    "classes",
    "classification",
    "lu",
    "lc",
    "lulc",
    "土地利用",
    "地类",
    "土地覆盖",
    "分类",
}

_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")


# --------------------------------------------------------------------- 数据结构


@dataclass
class RasterLayer:
    id: str
    dataset_id: str
    path: str
    title: str
    kind: str  # categorical | continuous
    year: int | None
    classes: dict[str, str]
    layer: Any  # QgsRasterLayer
    crs: str
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    resolution: tuple[float, float]
    extent: tuple[float, float, float, float]  # 图层自身 CRS
    extent_wgs84: tuple[float, float, float, float]
    file_size: int
    values: list[float] = field(default_factory=list)  # 离散型的取值列表
    source: str = "shared"  # shared | user
    scope: str = "shared"  # 完整空间标识：shared | user:<用户ID>
    #: QGIS 自己给这个图层配的渲染器（真彩色合成影像会是多波段彩色渲染器）。
    #: 「套配色」时要靠它还原 —— 一律换成单波段假彩色会把真彩色影像渲成一片紫。
    default_renderer: Any = None

    @property
    def ref(self) -> str:
        """全局唯一引用，形如 ``数据集:图层``。"""
        return f"{self.dataset_id}:{self.id}"

    def to_dict(self, detailed: bool = False) -> dict:
        data = {
            "ref": self.ref,
            "id": self.id,
            "dataset_id": self.dataset_id,
            "source": self.source,
            "scope": self.scope,
            "title": self.title,
            "kind": self.kind,
            "year": self.year,
            "file": Path(self.path).name,
            "path": self.path,
            "crs": self.crs,
            "width": self.width,
            "height": self.height,
            "band_count": self.band_count,
            "dtype": self.dtype,
            "nodata": self.nodata,
            "resolution": list(self.resolution),
            "extent": list(self.extent),
            "extent_wgs84": list(self.extent_wgs84),
            "file_size": self.file_size,
            "class_count": len(self.classes),
            # 真彩色合成影像（多波段，QGIS 认出来的是多波段彩色渲染器）。
            # 网页据此默认用「真彩色」而不是单波段假彩色 —— 遥感照片被拉成
            # 假彩色会整片发紫，跟 QGIS 桌面版完全不一样。
            "truecolor": is_truecolor_renderer(self.default_renderer),
        }
        if detailed:
            data["classes"] = self.classes
            data["values"] = self.values
        return data


@dataclass
class VectorLayer:
    id: str
    dataset_id: str
    path: str
    title: str
    layer: Any  # QgsVectorLayer
    crs: str
    feature_count: int
    geometry_type: str
    fields: list[dict]
    extent: tuple[float, float, float, float]  # WGS84
    file_size: int
    source: str = "shared"  # shared | user
    scope: str = "shared"  # 完整空间标识：shared | user:<用户ID>

    @property
    def ref(self) -> str:
        return f"{self.dataset_id}:{self.id}"

    def to_dict(self, detailed: bool = False) -> dict:
        data = {
            "ref": self.ref,
            "id": self.id,
            "dataset_id": self.dataset_id,
            "source": self.source,
            "scope": self.scope,
            "title": self.title,
            "file": Path(self.path).name,
            "path": self.path,
            "crs": self.crs,
            "feature_count": self.feature_count,
            "geometry_type": self.geometry_type,
            "extent_wgs84": list(self.extent),
            "file_size": self.file_size,
        }
        if detailed:
            data["fields"] = self.fields
        return data


@dataclass
class Dataset:
    id: str
    name: str
    description: str
    path: str
    crs: str
    layers: dict[str, RasterLayer] = field(default_factory=dict)
    vectors: dict[str, VectorLayer] = field(default_factory=dict)
    extent_wgs84: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source: str = "shared"  # shared | user
    scope: str = "shared"  # 完整空间标识：shared | user:<用户ID>

    def to_dict(self, detailed: bool = False) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "crs": self.crs,
            "source": self.source,
            "scope": self.scope,
            "extent_wgs84": list(self.extent_wgs84),
            "raster_count": len(self.layers),
            "vector_count": len(self.vectors),
        }
        if self.warnings:
            data["warnings"] = self.warnings
        if detailed:
            data["layers"] = {k: v.to_dict(detailed) for k, v in self.layers.items()}
            data["vectors"] = {k: v.to_dict(detailed) for k, v in self.vectors.items()}
        return data


# --------------------------------------------------------------------- 工具函数


def _wgs84_crs():
    from qgis.core import QgsCoordinateReferenceSystem

    return QgsCoordinateReferenceSystem("EPSG:4326")


def extent_to_wgs84(layer) -> tuple[float, float, float, float]:
    """把图层范围换算到 WGS84 经纬度。"""
    from qgis.core import QgsCoordinateTransform, QgsCoordinateTransformContext

    ext = layer.extent()
    crs = layer.crs()
    try:
        if crs.isValid() and crs != _wgs84_crs():
            tr = QgsCoordinateTransform(crs, _wgs84_crs(), QgsCoordinateTransformContext())
            ext = tr.transformBoundingBox(ext)
    except Exception:
        pass
    return (ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum())


def _ensure_crs(layer, name: str, warnings: list[str]) -> None:
    """图层没有坐标系信息时，按 EPSG:4326 解释。

    没有 CRS 的图层在 QGIS 里是**画不出来的**：渲染的目标 CRS 是 3857/4326，
    而图层自己的 CRS 无效 → 变换做不了 → 整层被跳过 → 出来一张全白的图。
    表现就是「上传成功、图层面板里也有、地图上什么都看不见」——
    最容易误判成「文件坏了」。

    QGIS 桌面版对未知 CRS 的图层也是按工程 CRS 处理的；本项目统一用 WGS84，
    所以这里补 EPSG:4326，并留一条告警让人知道「这是猜的」。
    """
    try:
        if layer.crs().isValid():
            return
    except Exception:
        return
    try:
        layer.setCrs(_wgs84_crs())
    except Exception as exc:
        warnings.append(f"图层 {name} 没有坐标系信息，且无法按 EPSG:4326 解释：{exc}")
        return
    warnings.append(f"图层 {name} 没有坐标系信息，已按 EPSG:4326 解释")


def _integer_type_codes() -> set[int]:
    """QGIS 里属于整型的 raster dataType 编码集合。"""
    codes: set[int] = set()
    try:
        from qgis.core import Qgis

        data_type = getattr(Qgis, "DataType", None)
        if data_type is not None:
            for name in dir(data_type):
                if name.startswith("_"):
                    continue
                value = getattr(data_type, name)
                if not isinstance(value, int):
                    continue
                if "Float" in name or "Double" in name:
                    continue
                if "Byte" in name or "Int" in name:
                    codes.add(value)
    except Exception:
        pass
    # 兜底：QgsRasterDataType 的经典取值
    codes |= {1, 2, 3, 4, 5, 14, 15}
    return codes


def _looks_categorical(name: str) -> bool:
    low = name.lower()
    tokens = set(re.split(r"[^0-9a-z一-鿿]+", low))
    tokens.add(low)
    return bool(tokens & _CATEGORICAL_HINTS)


def is_truecolor_renderer(renderer: Any) -> bool:
    """这个渲染器是不是 QGIS 认出来的**多波段彩色**渲染器（真彩色合成影像）。

    遥感照片、Sentinel-2 这类多波段影像，QGIS 桌面版会配一个
    「红/绿/蓝 = 1/2/3 波段」的 ``QgsMultiBandColorRenderer``。
    判据用 QGIS 的而不是自己数波段 —— QGIS 还认得出彩色表、带 alpha 的波段等等。

    这条判据决定网页端默认用真彩色还是单波段假彩色：
    拿单波段假彩色去渲真彩色照片，绿色通道会被压没（实测平均 RGB
    [28.6, 33.0, 37.4] → [50.8, 12.0, 71.0]），整片**发紫**。
    """
    if renderer is None:
        return False
    try:
        from qgis.core import QgsMultiBandColorRenderer

        return isinstance(renderer, QgsMultiBandColorRenderer)
    except Exception:
        return False


def _infer_year(name: str) -> int | None:
    m = _YEAR_RE.search(name)
    return int(m.group(1)) if m else None


def _infer_kind(layer, name: str) -> str:
    if _looks_categorical(name):
        return "categorical"
    try:
        from qgis.core import QgsRasterBandStats

        provider = layer.dataProvider()
        data_type = int(provider.dataType(1))
        if data_type in _integer_type_codes():
            stats = provider.bandStatistics(1, QgsRasterBandStats.All)
            if stats.minimumValue >= 0 and stats.maximumValue <= 64:
                return "categorical"
    except Exception:
        pass
    return "continuous"


def _sample_values(layer, nodata: float | None, max_pixels: int = 250_000) -> list[float]:
    """抽样读取离散的取值集合。"""
    try:
        provider = layer.dataProvider()
        width, height = int(layer.width()), int(layer.height())
        if width <= 0 or height <= 0:
            return []
        total = width * height
        scale = 1.0 if total <= max_pixels else math.sqrt(max_pixels / total)
        out_w = max(1, int(width * scale))
        out_h = max(1, int(height * scale))
        block = provider.block(1, layer.extent(), out_w, out_h)
        arr = block_to_numpy(block)
        return unique_values(arr, nodata)
    except Exception:
        return []


def _looks_like_geojson(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except Exception:
        return False
    return '"features"' in head or '"FeatureCollection"' in head or '"geometry"' in head


def _gpkg_sublayers(path: Path) -> list[str]:
    """列出 GeoPackage 里的子图层名。"""
    try:
        from osgeo import ogr

        source = ogr.Open(str(path))
        if source is None:
            return []
        names = []
        for index in range(source.GetLayerCount()):
            layer = source.GetLayerByIndex(index)
            if layer is not None:
                names.append(layer.GetName())
        return names
    except Exception:
        return []


def _load_metadata(dataset_dir: Path) -> dict:
    meta_file = dataset_dir / "metadata.json"
    if not meta_file.is_file():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_specs(raw: Any) -> dict[str, dict]:
    """metadata 里 layers/vectors 既支持 dict 也支持 list，统一成 dict。"""
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                spec = dict(value)
                spec.setdefault("id", key)
                out[str(key)] = spec
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = dict(item)
    return out


# --------------------------------------------------------------------- 注册中心


class Registry:
    """扫描数据根目录并维护数据集索引。

    支持多空间：
    - ``shared_root``：共享空间，所有登录用户可读可写
    - ``users_root/<用户ID>``：用户私有空间，只有本人可读写
    """

    def __init__(
        self,
        data_root: str | Path,
        shared_root: str | Path | None = None,
        users_root: str | Path | None = None,
    ):
        self.data_root = Path(data_root).expanduser().resolve()
        self.shared_root = (
            Path(shared_root).expanduser().resolve() if shared_root else self.data_root / "shared"
        )
        self.users_root = (
            Path(users_root).expanduser().resolve() if users_root else self.data_root / "users"
        )
        self._datasets: dict[str, Dataset] = {}
        self._scanned_at: float | None = None
        self._scan_errors: list[str] = []
        #: 「已经扫过了」标志。**跟扫到几个数据集无关** —— 见 ``_ensure_scanned``。
        self._scanned: bool = False

    @property
    def roots(self) -> list[tuple[Path, str]]:
        """所有数据空间：(目录, 空间标识)。"""
        out: list[tuple[Path, str]] = []
        if self.shared_root.is_dir():
            out.append((self.shared_root, "shared"))
        if self.users_root.is_dir():
            for child in sorted(self.users_root.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    out.append((child, f"user:{child.name}"))
        return out

    def space_root(self, scope: str) -> Path | None:
        """空间标识 -> 磁盘目录。"""
        for root, sc in self.roots:
            if sc == scope:
                return root
        return None

    def space_of(self, dataset_id: str) -> str | None:
        """数据集所属空间。"""
        ds = self._datasets.get(dataset_id)
        return ds.scope if ds else None

    def _scope_of_path(self, path: Path) -> str | None:
        """这个磁盘路径属于哪个空间？**不在数据根目录里就返回 None**。

        用来给「按路径引用图层」这条路上把门：只认数据根目录（共享空间 /
        各用户私有空间）里面的文件，服务器上其它位置一概不认。
        """
        try:
            resolved = path.resolve()
        except OSError:
            return None
        try:
            rel = resolved.relative_to(self.users_root.resolve())
        except ValueError:
            rel = None
        if rel is not None and rel.parts:
            return f"user:{rel.parts[0]}"
        try:
            resolved.relative_to(self.shared_root.resolve())
        except ValueError:
            return None
        return "shared"

    # ---------------------------------------------------------------- 扫描

    def scan(self) -> list[Dataset]:
        """重新扫描全部根目录。

        扫描会创建 QgsRasterLayer / QgsVectorLayer（QObject），必须与
        QgsApplication 同线程，因此只能通过 ``core.executor.run_qgis`` 调用。
        """
        from .. import qgis_env

        qgis_env.ensure_qgis_thread("数据集扫描")
        from qgis.core import QgsRasterLayer, QgsVectorLayer

        datasets: dict[str, Dataset] = {}
        errors: list[str] = []

        for root, scope in self.roots:
            if not root.is_dir():
                continue
            source = "shared" if scope == "shared" else "user"
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
                # 被标记删除的目录（文件还被占用时）跳过
                if (child / ".deleted").is_file():
                    continue
                try:
                    dataset = self._load_dataset(child, QgsRasterLayer, QgsVectorLayer, source, scope)
                except Exception as exc:
                    errors.append(f"{child.name}: {exc}")
                    continue
                if dataset is not None:
                    datasets[dataset.id] = dataset

        self._datasets = datasets
        self._scan_errors = errors
        self._scanned_at = _now()
        self._scanned = True
        self._purge_deleted()
        return list(datasets.values())

    def _purge_deleted(self) -> None:
        """清理各空间里被标记删除的数据集（文件句柄释放后真正删掉）。"""
        try:
            from .storage import Workspace

            for root, _scope in self.roots:
                if root.is_dir():
                    Workspace(root).purge()
        except Exception:
            pass

    def _load_dataset(
        self,
        directory: Path,
        raster_cls,
        vector_cls,
        source: str = "shared",
        scope: str = "shared",
    ) -> Dataset | None:
        meta = _load_metadata(directory)
        meta_layers = _normalize_specs(meta.get("layers"))
        meta_vectors = _normalize_specs(meta.get("vectors"))

        dataset = Dataset(
            id=directory.name,
            name=str(meta.get("name") or directory.name),
            description=str(meta.get("description") or ""),
            path=str(directory),
            crs=str(meta.get("crs") or ""),
            metadata=meta,
            source=source,
            scope=scope,
        )

        # 按 metadata 指定的文件优先，其余自动发现
        layer_specs: dict[str, tuple[Path, dict]] = {}
        vector_specs: dict[str, tuple[Path, dict]] = {}

        for spec_id, spec in meta_layers.items():
            file_name = spec.get("file") or f"{spec_id}.tif"
            candidate = directory / file_name
            if candidate.is_file():
                layer_specs[spec_id] = (candidate, spec)
        for spec_id, spec in meta_vectors.items():
            file_name = spec.get("file") or f"{spec_id}.geojson"
            candidate = directory / file_name
            if candidate.is_file():
                vector_specs[spec_id] = (candidate, spec)

        # 自动发现
        #
        # metadata 已经声明过的文件**不再重复登记**。一个多图层的 GeoPackage
        # 尤其容易中招：metadata 里按 id 声明了 land / boundaries / ...
        # 自动发现又会把同一批子图层展开成 basemap_land / basemap_boundaries / ...
        # 结果同一批数据在图层清单里出现两遍（后者还没有中文标题）。
        covered_vectors = {p for p, _ in vector_specs.values()}
        covered_rasters = {p for p, _ in layer_specs.values()}

        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in RASTER_EXTENSIONS:
                layer_id = path.stem
                if path not in covered_rasters:
                    layer_specs.setdefault(layer_id, (path, {}))
            elif suffix in VECTOR_EXTENSIONS:
                if suffix == ".json" and not _looks_like_geojson(path):
                    continue
                vector_id = path.stem
                if path not in covered_vectors:
                    vector_specs.setdefault(vector_id, (path, {}))
                # GeoPackage 可能含多个图层，逐个展开（metadata 声明过这个文件的就不展开）
                if suffix == ".gpkg" and path not in covered_vectors:
                    for sub in _gpkg_sublayers(path):
                        sub_id = f"{vector_id}_{sub}" if sub != vector_id else sub
                        vector_specs.setdefault(sub_id, (path, {"sub_layer": sub}))

        # 栅格
        for layer_id, (path, spec) in layer_specs.items():
            rlayer = raster_cls(str(path), layer_id, "gdal")
            if not rlayer.isValid():
                dataset.warnings.append(f"栅格无法加载：{path.name}")
                continue
            _ensure_crs(rlayer, path.name, dataset.warnings)
            provider = rlayer.dataProvider()
            try:
                nodata = provider.sourceNoDataValue(1)
            except Exception:
                nodata = None
            kind = str(spec.get("kind") or _infer_kind(rlayer, path.stem))
            classes = {str(k): str(v) for k, v in (spec.get("classes") or {}).items()}
            values = _sample_values(rlayer, nodata) if kind == "categorical" else []
            if kind == "categorical" and not classes and values:
                classes = {format_value(v): f"Class {format_value(v)}" for v in values}
            # 留住 QGIS 自己认出来的渲染器：多波段合成影像（真彩色）会配一个
            # 「红/绿/蓝 = 1/2/3 波段」的多波段彩色渲染器。后面套配色时要靠它还原。
            default_renderer = None
            try:
                renderer = rlayer.renderer()
                if renderer is not None and hasattr(renderer, "clone"):
                    default_renderer = renderer.clone()
            except Exception:
                default_renderer = None
            dataset.layers[layer_id] = RasterLayer(
                id=layer_id,
                dataset_id=dataset.id,
                path=str(path),
                title=str(spec.get("title") or layer_id),
                kind=kind,
                year=spec.get("year") or _infer_year(path.stem),
                classes=classes,
                layer=rlayer,
                crs=rlayer.crs().authid() or rlayer.crs().description(),
                width=int(rlayer.width()),
                height=int(rlayer.height()),
                band_count=int(rlayer.bandCount()),
                dtype=str(provider.dataType(1)),
                nodata=nodata,
                resolution=(
                    float(rlayer.rasterUnitsPerPixelX()),
                    float(rlayer.rasterUnitsPerPixelY()),
                ),
                extent=(
                    rlayer.extent().xMinimum(),
                    rlayer.extent().yMinimum(),
                    rlayer.extent().xMaximum(),
                    rlayer.extent().yMaximum(),
                ),
                extent_wgs84=extent_to_wgs84(rlayer),
                file_size=path.stat().st_size,
                values=values,
                source=source,
                scope=scope,
                default_renderer=default_renderer,
            )

        # 矢量
        for vector_id, (path, spec) in vector_specs.items():
            sub_layer = spec.get("sub_layer")
            ogr_path = f"{path}|layername={sub_layer}" if sub_layer else str(path)
            vlayer = vector_cls(ogr_path, vector_id, "ogr")
            if not vlayer.isValid():
                dataset.warnings.append(f"矢量无法加载：{path.name}")
                continue
            _ensure_crs(vlayer, path.name, dataset.warnings)
            fields = []
            for field in vlayer.fields():
                fields.append(
                    {
                        "name": field.name(),
                        "type": field.typeName(),
                        "alias": field.alias() or field.name(),
                    }
                )
            dataset.vectors[vector_id] = VectorLayer(
                id=vector_id,
                dataset_id=dataset.id,
                path=str(path),
                title=str(spec.get("title") or vector_id),
                layer=vlayer,
                crs=vlayer.crs().authid() or vlayer.crs().description(),
                feature_count=int(vlayer.featureCount()),
                geometry_type=vlayer.wkbType(),
                fields=fields,
                extent=extent_to_wgs84(vlayer),
                file_size=path.stat().st_size,
                source=source,
                scope=scope,
            )

        if not dataset.layers and not dataset.vectors:
            return None

        # 数据集整体范围 = 所有图层范围的并集（WGS84）
        boxes = [ly.extent_wgs84 for ly in dataset.layers.values()]
        boxes += [vy.extent for vy in dataset.vectors.values()]
        boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if boxes:
            dataset.extent_wgs84 = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
        if not dataset.crs:
            first = next(iter(dataset.layers.values()), None)
            if first:
                dataset.crs = first.crs
        return dataset

    # ---------------------------------------------------------------- 查询

    def _ensure_scanned(self) -> None:
        """确保索引已建立。

        扫描会创建图层对象（QObject），只能在 QGIS 工作线程上做。
        如果当前不在那条线程（例如 HTTP 请求线程），就把扫描派过去。

        **判据是「扫过了」，不是「扫到东西了」**
        ------------------------------------------------
        原来写的是 ``if self._datasets: return`` —— 也就是「数据集非空就当扫过了」。
        数据目录是**空**的时候这个判据永远不成立，于是**每次查询都重扫一遍**，
        而扫描必须排到那条**唯一的** QGIS 工作线程上。那条线程要是被一段
        卡死的控制台代码占着（控制台超时之后线程并不会停，只是没人再等它），
        这里的同步等待就会把**事件循环线程**一起焊死 —— 连 ``/healthz`` 都不回，
        整个服务看着像挂了。

        真出过这事：控制台里跑一个死循环，超时之后 ``/healthz`` 8 秒无响应。

        改成用「扫过了」这个标志之后，扫描只在启动时做一次；之后要刷新索引，
        走 ``/api/datasets/rescan``（或上传/删除后自动触发的那次）。
        """
        if self._scanned:
            return
        from .. import qgis_env
        from .executor import run_qgis_sync

        if qgis_env.in_qgis_thread():
            self.scan()
        else:
            run_qgis_sync(self.scan)

    def dataset_count(self, principal=None) -> int:
        """数据集数量（**不触发扫描**）。

        给存活探针用：``/healthz`` 必须是个**廉价**的调用 ——
        扫描要占 QGIS 工作线程，探针一旦去扫，遇到那条线程被占死时
        探针自己也就回不来了（外部监控会误判服务挂了）。
        """
        items = list(self._datasets.values())
        if principal is not None:
            items = [d for d in items if principal.can_see(d.scope)]
        return len(items)

    def datasets(self, principal=None) -> list[Dataset]:
        """数据集清单。``principal`` 为 None 时不过滤（管理员 / 内部调用）。"""
        self._ensure_scanned()
        items = list(self._datasets.values())
        if principal is not None:
            items = [d for d in items if principal.can_see(d.scope)]
        return items

    def get_dataset(self, dataset_id: str, principal=None) -> Dataset:
        self._ensure_scanned()
        dataset = self._datasets.get(dataset_id)
        if dataset is None or (principal is not None and not principal.can_see(dataset.scope)):
            raise NotFoundError(
                f"数据集不存在：{dataset_id}",
                {"available": sorted(d.id for d in self.datasets(principal))},
            )
        return dataset

    def get_raster(self, dataset_id: str, layer_id: str, principal=None) -> RasterLayer:
        dataset = self.get_dataset(dataset_id, principal)
        layer = dataset.layers.get(layer_id)
        if layer is None:
            raise NotFoundError(
                f"栅格图层不存在：{dataset_id}/{layer_id}",
                {"available": sorted(dataset.layers.keys())},
            )
        return layer

    def get_vector(self, dataset_id: str, vector_id: str, principal=None) -> VectorLayer:
        dataset = self.get_dataset(dataset_id, principal)
        layer = dataset.vectors.get(vector_id)
        if layer is None:
            raise NotFoundError(
                f"矢量图层不存在：{dataset_id}/{vector_id}",
                {"available": sorted(dataset.vectors.keys())},
            )
        return layer

    def forget(self, dataset_id: str) -> bool:
        """把数据集从索引里摘掉（释放图层对象，文件随后才能删）。"""
        from .. import qgis_env

        qgis_env.ensure_qgis_thread("释放图层")
        dataset = self._datasets.pop(dataset_id, None)
        if dataset is None:
            return False
        dataset.layers.clear()
        dataset.vectors.clear()
        return True

    def all_rasters(self, principal=None) -> list[RasterLayer]:
        out: list[RasterLayer] = []
        for dataset in self.datasets(principal):
            out.extend(dataset.layers.values())
        return out

    def all_vectors(self, principal=None) -> list[VectorLayer]:
        out: list[VectorLayer] = []
        for dataset in self.datasets(principal):
            out.extend(dataset.vectors.values())
        return out

    def resolve_ref(self, ref: str, principal=None):
        """按 ``数据集:图层`` 引用解析图层对象。

        也接受只写图层 id（全局唯一时）或图层在磁盘上的路径。
        """
        if not ref:
            raise NotFoundError("图层引用为空")

        # 1) 数据集:图层
        if ":" in ref:
            dataset_id, _, layer_id = ref.partition(":")
            dataset = self._datasets.get(dataset_id)
            if dataset is not None:
                # **要按调用者过滤**。原来直接取 ``self._datasets.get``，
                # 谁的私有数据集都能拿 —— 于是普通用户能拿别人的私有图层
                # 跑算法，产出的是别人数据的缓冲。
                if principal is not None and not principal.can_see(dataset.scope):
                    raise NotFoundError(f"图层不存在：{ref}")
                if layer_id in dataset.layers:
                    return dataset.layers[layer_id].layer
                if layer_id in dataset.vectors:
                    return dataset.vectors[layer_id].layer

        # 2) 裸图层 id
        for dataset in self.datasets(principal):
            if ref in dataset.layers:
                return dataset.layers[ref].layer
            if ref in dataset.vectors:
                return dataset.vectors[ref].layer

        # 3) 磁盘路径 —— **只认数据根目录里面的**。
        #
        # 原来任何绝对路径都收，于是普通用户能指着服务器上任意文件
        # （包括别的用户的私有数据）让 QGIS 去读。
        candidate = Path(ref)
        scope = self._scope_of_path(candidate)
        if scope is None:
            raise NotFoundError(f"图层不存在：{ref}")
        if principal is not None and not principal.can_see(scope):
            raise NotFoundError(f"图层不存在：{ref}")
        if candidate.is_file():
            from qgis.core import QgsRasterLayer, QgsVectorLayer

            suffix = candidate.suffix.lower()
            if suffix in RASTER_EXTENSIONS:
                layer = QgsRasterLayer(str(candidate), candidate.stem, "gdal")
                if layer.isValid():
                    return layer
            else:
                layer = QgsVectorLayer(str(candidate), candidate.stem, "ogr")
                if layer.isValid():
                    return layer

        raise NotFoundError(
            f"找不到图层：{ref}",
            {"hint": "用「数据集:图层」形式，或先在 /api/layers 里查可用引用"},
        )

    def layer_refs(self, principal=None) -> list[dict]:
        """全部图层的扁平清单（含 ref），给算法参数表单用。"""
        items: list[dict] = []
        for dataset in self.datasets(principal):
            for layer in dataset.layers.values():
                items.append(
                    {
                        "ref": layer.ref,
                        "title": layer.title,
                        "kind": "raster",
                        "layer_kind": layer.kind,
                        # 真彩色合成影像：网页据此默认用真彩色而不是单波段假彩色
                        "truecolor": is_truecolor_renderer(layer.default_renderer),
                        "dataset_id": dataset.id,
                        "dataset_name": dataset.name,
                        "source": layer.source,
                        "scope": layer.scope,
                        "crs": layer.crs,
                        "extent_wgs84": list(layer.extent_wgs84),
                    }
                )
            for vector in dataset.vectors.values():
                items.append(
                    {
                        "ref": vector.ref,
                        "title": vector.title,
                        "kind": "vector",
                        "layer_kind": vector.geometry_type,
                        "dataset_id": dataset.id,
                        "dataset_name": dataset.name,
                        "source": vector.source,
                        "scope": vector.scope,
                        "crs": vector.crs,
                        "feature_count": vector.feature_count,
                        "extent_wgs84": list(vector.extent),
                    }
                )
        return items

    @property
    def scanned_at(self) -> float | None:
        return self._scanned_at

    @property
    def scan_errors(self) -> list[str]:
        return list(self._scan_errors)


def format_value(value: float) -> str:
    """离散取值 -> 稳定的字符串键（整数不带 .0）。"""
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _now() -> float:
    import time

    return time.time()
