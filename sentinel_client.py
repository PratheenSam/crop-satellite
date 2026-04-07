"""
sentinel_client.py  (Microsoft Planetary Computer backend)
-----------------------------------------------------------
Fetches Sentinel-2 L2A imagery via the Planetary Computer STAC catalog and
Azure-hosted Cloud-Optimised GeoTIFFs.

No credentials or account are required.  The `planetary_computer` package
automatically obtains short-lived Azure SAS tokens for the public COG files.

Band layout returned (axis 2):
    0  B02  Blue          10 m
    1  B03  Green         10 m
    2  B04  Red           10 m
    3  B05  Red-Edge 1    20 m  (resampled to 10 m)
    4  B08  NIR broad     10 m
    5  B8A  NIR narrow    20 m  (resampled)
    6  B11  SWIR-1        20 m  (resampled)
    7  B12  SWIR-2        20 m  (resampled)
    8  SCL  Scene class   20 m  (nearest-neighbour to 10 m)
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Tuple

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from rasterio.crs import CRS as RioCRS
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

from bbox import BBox

# Speed up GDAL's HTTP/COG access
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")

CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"

# Order MUST match image_analyzer.py band index constants
BAND_ORDER = ["B02", "B03", "B04", "B05", "B08", "B8A", "B11", "B12", "SCL"]

# Multiply raw DN by this to get reflectance (not applied to SCL)
_SCALE = 1e-4

# Maximum pixels on each axis sent back by the API
_MAX_PIXELS = 2500

# SCL classes treated as invalid / cloudy
_CLOUD_SCL_VALUES = {0, 1, 3, 8, 9, 10, 11}

_WGS84 = RioCRS.from_epsg(4326)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _target_size(bbox: BBox, resolution_m: int = 10) -> Tuple[int, int]:
    """Estimate output pixel dimensions at *resolution_m*, capped at _MAX_PIXELS."""
    lat_mid = (bbox.min_y + bbox.max_y) / 2.0
    m_per_deg_lon = 111_320 * math.cos(math.radians(lat_mid))
    m_per_deg_lat = 110_540
    w = int((bbox.max_x - bbox.min_x) * m_per_deg_lon / resolution_m)
    h = int((bbox.max_y - bbox.min_y) * m_per_deg_lat / resolution_m)
    w = max(1, min(w, _MAX_PIXELS))
    h = max(1, min(h, _MAX_PIXELS))
    return w, h


def _get_asset(item, band_name: str):
    """Return asset for *band_name*, trying common key variants."""
    for key in (band_name, band_name.lower(), band_name.upper()):
        if key in item.assets:
            return item.assets[key]
    available = list(item.assets.keys())
    raise KeyError(
        f"Band '{band_name}' not found in STAC item '{item.id}'. "
        f"Available assets: {available}"
    )


def _read_band(
    href: str,
    bbox: BBox,
    target_width: int,
    target_height: int,
    is_scl: bool = False,
) -> np.ndarray:
    """
    Read one COG band from *href*, clipped to *bbox* and resampled to
    (target_height × target_width).
    """
    bbox_tuple = (bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y)
    with rasterio.open(href) as src:
        bounds_native = transform_bounds(_WGS84, src.crs, *bbox_tuple)
        win = window_from_bounds(*bounds_native, src.transform)
        resamp = Resampling.nearest if is_scl else Resampling.bilinear
        data = src.read(
            1,
            window=win,
            out_shape=(target_height, target_width),
            resampling=resamp,
            boundless=True,
            fill_value=0,
        )
    arr = data.astype(np.float32)
    if not is_scl:
        arr *= _SCALE  # DN → reflectance (0–1)
    return arr


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _farm_cloud_pct(scl: np.ndarray) -> float:
    """Return % of pixels that are cloudy/invalid in the SCL array."""
    total = scl.size
    if total == 0:
        return 100.0
    cloudy = sum(int(np.sum(scl == v)) for v in _CLOUD_SCL_VALUES)
    return round(100.0 * cloudy / total, 2)


def fetch_latest_image(
    bbox: BBox,
    max_cloud_cover: float = 20.0,
    lookback_days: int = 60,
    max_farm_cloud_cover: float = 40.0,
) -> Tuple[np.ndarray, dict]:
    """
    Fetch the most recent Sentinel-2 L2A image for *bbox* whose cloud cover
    **over the farm bbox specifically** is below *max_farm_cloud_cover*.

    *max_cloud_cover* pre-filters the catalog (scene-level, fast).
    *max_farm_cloud_cover* post-filters by downloading the SCL band and
    measuring cloud % over just the bounding box (exact, per-farm).

    No credentials required.

    Returns
    -------
    image    : np.ndarray  shape (height, width, 9)  float32
    metadata : dict        acquisition_date, cloud_cover_pct,
                           farm_cloud_cover_pct, image_id, width, height
    """
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=lookback_days)

    catalog = pystac_client.Client.open(
        CATALOG_URL,
        modifier=planetary_computer.sign_inplace,
    )

    def _search(cloud_max: float) -> list:
        search = catalog.search(
            collections=[COLLECTION],
            bbox=bbox.as_list(),
            datetime=f"{start_date}/{end_date}",
            query={"eo:cloud_cover": {"lt": cloud_max}},
            max_items=50,
        )
        items = list(search.items())
        items.sort(key=lambda x: x.datetime or datetime.min, reverse=True)
        return items

    items = _search(max_cloud_cover)
    if not items:
        relaxed = min(max_cloud_cover * 2, 80.0)
        items = _search(relaxed)
    if not items:
        raise ValueError(
            f"No Sentinel-2 L2A images found within the last {lookback_days} days "
            f"for bbox {bbox}. Try increasing lookback_days."
        )

    width, height = _target_size(bbox)

    # Iterate candidates newest-first; pick first whose farm-level cloud % is acceptable
    best = None
    best_farm_cc = 100.0
    chosen_item = None

    for item in items:
        scl_href = _get_asset(item, "SCL").href
        scl = _read_band(scl_href, bbox, width, height, is_scl=True)
        farm_cc = _farm_cloud_pct(scl)
        if best is None or farm_cc < best_farm_cc:
            best = scl
            best_farm_cc = farm_cc
            chosen_item = item
        if farm_cc <= max_farm_cloud_cover:
            break  # good enough — stop searching

    acquisition_date: str = chosen_item.datetime.strftime("%Y-%m-%d")
    scene_cloud_cover: float = float(chosen_item.properties.get("eo:cloud_cover", 0.0))

    # Now fetch all bands for the chosen item in parallel
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_band(band_name):
        asset = _get_asset(chosen_item, band_name)
        return _read_band(
            asset.href, bbox, width, height, is_scl=(band_name == "SCL")
        )

    with ThreadPoolExecutor(max_workers=9) as executor:
        bands = list(executor.map(_fetch_band, BAND_ORDER))

    image = np.stack(bands, axis=-1)  # (H, W, 9)

    return image, {
        "acquisition_date": acquisition_date,
        "cloud_cover_pct": round(scene_cloud_cover, 2),
        "farm_cloud_cover_pct": best_farm_cc,
        "image_id": chosen_item.id,
        "width": width,
        "height": height,
    }


def cloud_mask_from_scl(scl_band: np.ndarray) -> np.ndarray:
    """Return a boolean mask (True = cloudy / invalid) from the SCL band."""
    mask = np.zeros(scl_band.shape, dtype=bool)
    for val in _CLOUD_SCL_VALUES:
        mask |= scl_band == val
    return mask

