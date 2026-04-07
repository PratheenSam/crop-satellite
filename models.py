from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Optional


class FarmRequest(BaseModel):
    coordinates: List[List[float]] = Field(
        ...,
        description=(
            "List of [latitude, longitude] pairs (WGS84) forming a closed polygon. "
            "Minimum 3 vertices required."
        ),
        examples=[
            [
                [20.5937, 78.9629],
                [20.5937, 78.9729],
                [20.6037, 78.9729],
                [20.6037, 78.9629],
            ]
        ],
    )
    max_cloud_cover: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="Maximum acceptable scene-level cloud cover % (filters the catalog).",
    )
    max_farm_cloud_cover: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description=(
            "Maximum acceptable cloud cover % measured over your farm specifically. "
            "The service checks each candidate scene's SCL band over the farm bbox "
            "and picks the least-cloudy scene that meets this threshold."
        ),
    )
    lookback_days: int = Field(
        default=60,
        ge=1,
        le=365,
        description="How many days back to search for the most recent cloud-free image.",
    )
    farm_id: Optional[int] = Field(
        default=None,
        description="Optional ID of the farm to associate the analysis result with in the DB."
    )

    @field_validator("coordinates")
    @classmethod
    def validate_polygon(cls, v: List[List[float]]) -> List[List[float]]:
        if len(v) < 3:
            raise ValueError("Farm polygon must contain at least 3 coordinate pairs.")
        for pair in v:
            if len(pair) != 2:
                raise ValueError(
                    f"Each coordinate must be exactly [latitude, longitude], got: {pair}"
                )
            lat, lon = pair
            if not (-90.0 <= lat <= 90.0):
                raise ValueError(f"Latitude {lat} is out of range [-90, 90].")
            if not (-180.0 <= lon <= 180.0):
                raise ValueError(f"Longitude {lon} is out of range [-180, 180].")
        return v


class StressMetrics(BaseModel):
    ndvi: float = Field(..., description="Normalized Difference Vegetation Index (−1 to 1).")
    ndwi: float = Field(..., description="Normalized Difference Water Index (−1 to 1).")
    ndre: float = Field(..., description="Normalized Difference Red-Edge Index (−1 to 1).")
    evi: float = Field(..., description="Enhanced Vegetation Index.")
    savi: float = Field(..., description="Soil-Adjusted Vegetation Index.")
    msavi: float = Field(..., description="Modified SAVI (self-adjusting).")
    ndmi: float = Field(..., description="Normalized Difference Moisture Index (−1 to 1).")
    bsi: float = Field(..., description="Bare Soil Index (−1 to 1).")
    gndvi: float = Field(..., description="Green Normalized Difference Vegetation Index.")
    cire: float = Field(..., description="Chlorophyll Index Red-Edge.")


class StressPoint(BaseModel):
    coordinates: List[float] = Field(
        ..., description="[latitude, longitude] centroid of the stressed zone."
    )
    severity: str = Field(
        ..., description="Severity level: low | medium | high | critical."
    )
    area_hectares: float = Field(..., description="Estimated affected area in hectares.")
    pixel_count: int = Field(..., description="Number of 10 m pixels in this zone.")
    possible_causes: List[str] = Field(
        ..., description="Ranked list of likely causes inferred from spectral indices."
    )
    metrics: StressMetrics


class FieldSummary(BaseModel):
    mean_ndvi: float
    mean_ndwi: float
    mean_ndre: float
    mean_evi: float
    mean_ndmi: float
    healthy_area_pct: float = Field(..., description="% of farm with NDVI ≥ 0.5.")
    moderate_area_pct: float = Field(..., description="% of farm with NDVI 0.35–0.5.")
    stressed_area_pct: float = Field(..., description="% of farm with NDVI < 0.35.")
    cloud_cover_pct: float = Field(..., description="% of bounding box covered by clouds.")
    adaptive_stress_threshold: float = Field(
        ...,
        description=(
            "NDVI threshold used for stress detection, adapted to this field's "
            "own mean and std. Lower than the global 0.35 floor for healthy fields, "
            "which improves sensitivity to within-field variation on small plots."
        ),
    )


class AnalysisResponse(BaseModel):
    image_date: str = Field(..., description="Acquisition date of the Sentinel-2 scene (YYYY-MM-DD).")
    satellite: str = "Sentinel-2 L2A"
    resolution_m: int = 10
    bbox: Dict[str, float] = Field(
        ..., description="Bounding box of the fetched scene: min_lon, min_lat, max_lon, max_lat."
    )
    image_cloud_cover_pct: float
    farm_cloud_cover_pct: float = Field(
        ..., description="Actual cloud cover % measured over the farm polygon."
    )
    total_stress_zones: int
    stress_points: List[StressPoint]
    field_summary: FieldSummary
    warnings: List[str] = []


class FarmerCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=10)
    village: str = Field(..., min_length=1)
    mandal: Optional[str] = None
    district: Optional[str] = None


class FarmerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    village: Optional[str] = None
    mandal: Optional[str] = None
    district: Optional[str] = None


class FarmCreate(BaseModel):
    farmer_id: int
    crop_type: str
    sowing_date: str
    soil_type: str
    irrigation_type: str
    coordinates: List[List[float]]
    area_acres: float
