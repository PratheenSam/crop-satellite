"""
image_analyzer.py
-----------------
Spectral index computation, stress-zone detection, and cause classification
for Sentinel-2 L2A imagery.

Band layout expected from sentinel_client.EVALSCRIPT:
    0  B02  Blue
    1  B03  Green
    2  B04  Red
    3  B05  Red-Edge 1
    4  B08  NIR (broad)
    5  B8A  NIR (narrow)
    6  B11  SWIR-1
    7  B12  SWIR-2
    8  SCL  Scene Classification Layer
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from scipy import ndimage
from scipy.ndimage import gaussian_filter
from skimage.draw import polygon as draw_polygon

from bbox import BBox

from sentinel_client import cloud_mask_from_scl

# ---------------------------------------------------------------------------
# Band indices
# ---------------------------------------------------------------------------
B_BLUE = 0
B_GREEN = 1
B_RED = 2
B_RE1 = 3   # Red-Edge 705 nm
B_NIR = 4   # NIR 842 nm
B_NIRN = 5  # NIR narrow 865 nm
B_SWIR1 = 6
B_SWIR2 = 7
B_SCL = 8

# Pixel area at 10 m resolution
_PIXEL_AREA_HA = 0.01  # 10m × 10m = 100 m² = 0.01 ha

# Absolute minimum zone size regardless of farm size
_ABS_MIN_PIXELS = 3   # 300 m² — smallest reportable patch

# Fraction of total farm pixels used as adaptive minimum zone size
# e.g. for a 200-px farm → min zone = max(3, 200*0.01) = 3 px (0.03 ha)
#      for a 5000-px farm → min zone = max(3, 5000*0.01) = 50 px (0.5 ha)
_ZONE_FRACTION = 0.01

EPS = 1e-9


# ---------------------------------------------------------------------------
# Spectral indices
# ---------------------------------------------------------------------------

def compute_indices(image: np.ndarray) -> Dict[str, np.ndarray]:
    """
    Compute all spectral indices from a (H, W, 9) float32 Sentinel-2 array.

    Returns a dict of 2-D numpy arrays, all same shape (H, W).
    """
    b = image[:, :, B_BLUE].astype(np.float32)
    g = image[:, :, B_GREEN].astype(np.float32)
    r = image[:, :, B_RED].astype(np.float32)
    re1 = image[:, :, B_RE1].astype(np.float32)
    nir = image[:, :, B_NIR].astype(np.float32)
    sw1 = image[:, :, B_SWIR1].astype(np.float32)
    scl = image[:, :, B_SCL]

    # ------------------------------------------------------------------
    # NDVI  – vegetation health; healthy > 0.5, stressed < 0.35
    ndvi = (nir - r) / (nir + r + EPS)

    # NDWI  – canopy water content (Gao 1996); drought < −0.1
    ndwi = (g - nir) / (g + nir + EPS)

    # NDRE  – red-edge chlorophyll; deficiency < 0.3
    ndre = (nir - re1) / (nir + re1 + EPS)

    # EVI   – enhanced vegetation (blue corrects aerosols)
    evi = 2.5 * (nir - r) / (nir + 6.0 * r - 7.5 * b + 1.0 + EPS)

    # SAVI  – soil-adjusted vegetation (L = 0.5)
    L = 0.5
    savi = ((nir - r) / (nir + r + L + EPS)) * (1.0 + L)

    # MSAVI – modified SAVI (self-adjusting, no L needed)
    msavi = (2.0 * nir + 1.0 - np.sqrt(np.maximum((2.0 * nir + 1.0) ** 2 - 8.0 * (nir - r), 0.0))) / 2.0

    # NDMI  – moisture index; water stress < −0.1
    ndmi = (nir - sw1) / (nir + sw1 + EPS)

    # BSI   – bare soil index; high values = exposed soil
    bsi = ((sw1 + r) - (nir + b)) / ((sw1 + r) + (nir + b) + EPS)

    # GNDVI – green-NDVI, more sensitive to chlorophyll variation
    gndvi = (nir - g) / (nir + g + EPS)

    # CIre  – chlorophyll index red-edge (Gitelson 2003)
    cire = (nir / (re1 + EPS)) - 1.0

    # Cloud / invalid mask
    cloud_mask = cloud_mask_from_scl(scl)

    return {
        "ndvi": ndvi,
        "ndwi": ndwi,
        "ndre": ndre,
        "evi": evi,
        "savi": savi,
        "msavi": msavi,
        "ndmi": ndmi,
        "bsi": bsi,
        "gndvi": gndvi,
        "cire": cire,
        "cloud_mask": cloud_mask,
    }


# ---------------------------------------------------------------------------
# Farm polygon mask
# ---------------------------------------------------------------------------

def build_farm_mask(
    coordinates: List[List[float]],
    bbox: BBox,
    height: int,
    width: int,
) -> np.ndarray:
    """
    Rasterize the farm polygon into a boolean (H, W) mask.

    Parameters
    ----------
    coordinates : list of [lon, lat] pairs (WGS84)
    bbox        : BBox of the fetched image
    height, width : image dimensions in pixels
    """
    cols = [
        int((lon - bbox.min_x) / (bbox.max_x - bbox.min_x) * width)
        for _, lon in coordinates
    ]
    rows = [
        int((bbox.max_y - lat) / (bbox.max_y - bbox.min_y) * height)
        for lat, _ in coordinates
    ]

    # Clip to valid pixel range
    cols = [max(0, min(width - 1, c)) for c in cols]
    rows = [max(0, min(height - 1, r)) for r in rows]

    rr, cc = draw_polygon(rows, cols, shape=(height, width))
    mask = np.zeros((height, width), dtype=bool)
    mask[rr, cc] = True
    return mask


def _pixel_to_lonlat(
    row: float, col: float, bbox: BBox, height: int, width: int
) -> Tuple[float, float]:
    """Convert (fractional) pixel centre to (longitude, latitude)."""
    lon = bbox.min_x + (col + 0.5) / width * (bbox.max_x - bbox.min_x)
    lat = bbox.max_y - (row + 0.5) / height * (bbox.max_y - bbox.min_y)
    return round(lon, 6), round(lat, 6)


# ---------------------------------------------------------------------------
# Cause classification
# ---------------------------------------------------------------------------

@dataclass
class _ZoneMetrics:
    ndvi: float
    ndwi: float
    ndre: float
    evi: float
    savi: float
    msavi: float
    ndmi: float
    bsi: float
    gndvi: float
    cire: float


def _classify_causes(m: _ZoneMetrics) -> Tuple[str, List[str]]:
    """
    Derive severity and a ranked list of possible causes from zone-averaged indices.

    Thresholds are informed by peer-reviewed remote-sensing literature and
    standard agronomy practice.
    """
    causes: List[str] = []
    severity_score = 0

    # ---- Drought / water stress ------------------------------------------
    if m.ndmi < -0.3 and m.ndwi < -0.3:
        causes.append(
            "Severe drought / water stress — very low canopy moisture (NDMI and NDWI both strongly negative)"
        )
        severity_score += 4
    elif m.ndmi < -0.1 or m.ndwi < -0.1:
        causes.append(
            "Mild-to-moderate water stress — reduced canopy moisture content"
        )
        severity_score += 2

    # ---- Chlorophyll / nutrient deficiency --------------------------------
    if m.cire < 1.0 and m.ndre < 0.25:
        causes.append(
            "Chlorophyll deficiency — likely nitrogen or magnesium deficiency; "
            "consider soil N/Mg testing and foliar application"
        )
        severity_score += 3
    elif m.gndvi < 0.3 and m.ndre < 0.35:
        causes.append(
            "Reduced green-leaf pigmentation — possible early-stage nutrient stress "
            "(N, Fe, or Mg) or senescence"
        )
        severity_score += 2

    # ---- Bare soil / crop failure -----------------------------------------
    if m.bsi > 0.15 and m.ndvi < 0.2:
        causes.append(
            "Bare soil exposure — crop failure, non-emergence, stand loss, "
            "or recently harvested section"
        )
        severity_score += 4

    # ---- Waterlogging / flooding ------------------------------------------
    if m.ndwi > 0.1 and m.ndmi > 0.25 and m.ndvi < 0.45:
        causes.append(
            "Waterlogging or surface flooding — excess soil moisture restricting "
            "root oxygen; check drainage and field topography"
        )
        severity_score += 3

    # ---- Disease / pest damage -------------------------------------------
    if 0.15 < m.ndvi < 0.40 and m.ndre < 0.38 and -0.05 < m.ndmi < 0.15:
        causes.append(
            "Possible disease or pest infestation — vegetation vigor suppressed "
            "without concurrent moisture stress (e.g., fungal blight, aphids, rust)"
        )
        severity_score += 2

    # ---- Soil salinity (indirect) ----------------------------------------
    if m.bsi > 0.2 and m.ndvi < 0.25 and m.ndre < 0.28:
        causes.append(
            "Possible soil salinity or alkalinity — poor stand density with low "
            "reflectance in red-edge; recommend EC soil testing"
        )
        severity_score += 2

    # ---- Weed pressure (relatively healthy dense non-crop vegetation) -----
    if m.ndvi > 0.55 and m.bsi < -0.1 and m.cire > 3.0:
        causes.append(
            "Possible dense weed patch — very high NDVI in localised zone "
            "may indicate non-crop vegetation outcompeting the crop"
        )
        severity_score += 1

    # ---- Fallback --------------------------------------------------------
    if not causes:
        causes.append(
            "General vegetation stress — NDVI below healthy threshold; "
            "investigate crop history, irrigation records, and field scouting"
        )
        severity_score += 1

    # ---- Severity tier ---------------------------------------------------
    if severity_score >= 7:
        severity = "critical"
    elif severity_score >= 4:
        severity = "high"
    elif severity_score >= 2:
        severity = "medium"
    else:
        severity = "low"

    return severity, causes


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

@dataclass
class StressZone:
    lon: float
    lat: float
    severity: str
    area_hectares: float
    pixel_count: int
    possible_causes: List[str]
    metrics: Dict[str, float]


@dataclass
class AnalysisResult:
    stress_zones: List[StressZone] = field(default_factory=list)
    field_summary: Dict[str, float] = field(default_factory=dict)
    cloud_cover_pct: float = 0.0
    warnings: List[str] = field(default_factory=list)


def analyze(
    image: np.ndarray,
    bbox: BBox,
    farm_coordinates: List[List[float]],
) -> AnalysisResult:
    """
    Full pipeline: compute indices → detect stress zones → classify causes.

    Parameters
    ----------
    image           : (H, W, 9) float32 Sentinel-2 array from sentinel_client
    bbox            : BBox of the fetched image
    farm_coordinates: polygon vertices as [[lon, lat], ...]
    """
    height, width = image.shape[:2]
    result = AnalysisResult()

    # ---- Spectral indices ------------------------------------------------
    idx = compute_indices(image)
    cloud_mask: np.ndarray = idx["cloud_mask"]

    # ---- Farm boundary mask ----------------------------------------------
    farm_mask = build_farm_mask(farm_coordinates, bbox, height, width)
    if not farm_mask.any():
        result.warnings.append(
            "Farm polygon could not be rasterized (may be too small for the image resolution). "
            "Falling back to full bounding box."
        )
        farm_mask = np.ones((height, width), dtype=bool)

    # ---- Valid pixel mask (inside farm, not cloudy, not no-data) ----------
    valid_mask = farm_mask & ~cloud_mask & (image[:, :, B_NIR] > 0.0)

    total_valid_px = int(valid_mask.sum())
    total_farm_px = int(farm_mask.sum())
    cloud_px = int((farm_mask & cloud_mask).sum())

    if total_farm_px > 0:
        result.cloud_cover_pct = round(100.0 * cloud_px / total_farm_px, 2)
    if total_valid_px == 0:
        result.warnings.append(
            "No valid (cloud-free, inside-farm) pixels found. "
            "Try increasing lookback_days or max_cloud_cover."
        )
        return result

    # ---- Stress detection ------------------------------------------------
    ndvi = idx["ndvi"]

    # Smooth NDVI slightly before thresholding to suppress single-pixel noise.
    # sigma=0.8 at 10 m ≈ one-pixel neighbourhood — removes salt-and-pepper
    # without blurring real zone edges (important for small fields).
    ndvi_smooth = gaussian_filter(ndvi.astype(np.float64), sigma=0.8).astype(np.float32)
    # Restore NaN-equivalent (zero/invalid) pixels to avoid smoothing-in fake values
    ndvi_smooth = np.where(valid_mask, ndvi_smooth, ndvi)

    # --- Relative threshold (adaptive to field conditions) ----------------
    # Use the field's own mean and std so that within-field variation is caught
    # even when the absolute NDVI values are moderate.
    # Stressed pixel = more than 1 std below the field mean  AND  below 0.5
    # Hard floor at 0.35 to always catch severe stress (bare soil, dead crop).
    field_ndvi_vals = ndvi_smooth[valid_mask]
    if field_ndvi_vals.size > 0:
        field_mean = float(np.nanmean(field_ndvi_vals))
        field_std  = float(np.nanstd(field_ndvi_vals))
        relative_threshold = field_mean - field_std      # 1 std below mean
        absolute_floor     = 0.35                        # always catch severe stress
        stress_threshold   = min(relative_threshold, absolute_floor)
    else:
        stress_threshold = 0.35

    # Pixel-level stress criteria (any one is sufficient)
    low_ndvi       = ndvi_smooth < stress_threshold
    moisture_stress = (idx["ndmi"] < -0.2) & (idx["ndwi"] < -0.2)
    bare_soil      = (idx["bsi"] > 0.1) & (ndvi_smooth < 0.3)

    stress_mask = valid_mask & (low_ndvi | moisture_stress | bare_soil)

    # ---- Connected-component labelling ------------------------------------
    labeled, n_labels = ndimage.label(stress_mask)

    # Adaptive minimum zone size: 1% of valid farm pixels, floored at 3 px
    min_zone_px = max(_ABS_MIN_PIXELS, int(total_valid_px * _ZONE_FRACTION))

    if total_valid_px < 40:
        result.warnings.append(
            f"Farm has only {total_valid_px} valid pixels (~{total_valid_px * _PIXEL_AREA_HA:.2f} ha "
            f"at 10 m). Fields smaller than ~0.4 ha (1 acre) may not have enough pixels for "
            "reliable analysis. Consider using a larger bounding area or a higher-resolution source."
        )

    # Helper: mean of an index array over an arbitrary pixel mask
    def _zone_mean(arr: np.ndarray, mask: np.ndarray) -> float:
        vals = arr[mask]
        return round(float(np.nanmean(vals)), 4) if vals.size else 0.0

    for zone_id in range(1, n_labels + 1):
        zone_px = labeled == zone_id
        px_count = int(zone_px.sum())
        if px_count < min_zone_px:
            continue

        # Centroid in pixel space
        cy, cx = ndimage.center_of_mass(zone_px)
        lon, lat = _pixel_to_lonlat(cy, cx, bbox, height, width)

        zm = _ZoneMetrics(
            ndvi =_zone_mean(idx["ndvi"],  zone_px),
            ndwi =_zone_mean(idx["ndwi"],  zone_px),
            ndre =_zone_mean(idx["ndre"],  zone_px),
            evi  =_zone_mean(idx["evi"],   zone_px),
            savi =_zone_mean(idx["savi"],  zone_px),
            msavi=_zone_mean(idx["msavi"], zone_px),
            ndmi =_zone_mean(idx["ndmi"],  zone_px),
            bsi  =_zone_mean(idx["bsi"],   zone_px),
            gndvi=_zone_mean(idx["gndvi"], zone_px),
            cire =_zone_mean(idx["cire"],  zone_px),
        )

        severity, causes = _classify_causes(zm)

        result.stress_zones.append(
            StressZone(
                lon=lon,
                lat=lat,
                severity=severity,
                area_hectares=round(px_count * _PIXEL_AREA_HA, 4),
                pixel_count=px_count,
                possible_causes=causes,
                metrics={
                    "ndvi": zm.ndvi,
                    "ndwi": zm.ndwi,
                    "ndre": zm.ndre,
                    "evi": zm.evi,
                    "savi": zm.savi,
                    "msavi": zm.msavi,
                    "ndmi": zm.ndmi,
                    "bsi": zm.bsi,
                    "gndvi": zm.gndvi,
                    "cire": zm.cire,
                },
            )
        )

    # ---- Field summary ---------------------------------------------------
    def _field_mean(arr: np.ndarray) -> float:
        return round(float(np.nanmean(arr[valid_mask])), 4)

    healthy_px  = int((valid_mask & (ndvi >= 0.5)).sum())
    moderate_px = int((valid_mask & (ndvi >= 0.35) & (ndvi < 0.5)).sum())
    stressed_px = int((valid_mask & (ndvi < 0.35)).sum())

    result.field_summary = {
        "mean_ndvi": _field_mean(idx["ndvi"]),
        "mean_ndwi": _field_mean(idx["ndwi"]),
        "mean_ndre": _field_mean(idx["ndre"]),
        "mean_evi":  _field_mean(idx["evi"]),
        "mean_ndmi": _field_mean(idx["ndmi"]),
        "healthy_area_pct":  round(100.0 * healthy_px  / total_valid_px, 2),
        "moderate_area_pct": round(100.0 * moderate_px / total_valid_px, 2),
        "stressed_area_pct": round(100.0 * stressed_px / total_valid_px, 2),
        "cloud_cover_pct":   result.cloud_cover_pct,
        "adaptive_stress_threshold": round(stress_threshold, 4),
    }

    if result.field_summary["cloud_cover_pct"] > 15:
        result.warnings.append(
            f"Cloud cover over the farm is {result.field_summary['cloud_cover_pct']:.1f}%. "
            "Stress analysis is based on cloud-free pixels only; results may be incomplete."
        )

    return result
