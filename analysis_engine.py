import logging
from sqlalchemy.orm import Session
from fastapi.concurrency import run_in_threadpool

from bbox import BBox
from sentinel_client import fetch_latest_image
from image_analyzer import AnalysisResult, StressZone, analyze
from models import (
    AnalysisResponse,
    StressMetrics,
    StressPoint,
    FieldSummary,
)
from models_db import Farm as DBFarm, AnalysisRecord as DBAnalysisRecord

logger = logging.getLogger("crop-satellite")

async def perform_farm_analysis(
    db: Session,
    farm_id: int,
    max_cloud_cover: float = 20.0,
    lookback_days: int = 30,
    max_farm_cloud_cover: float = 50.0,
) -> AnalysisResponse:
    """
    Core analysis logic extracted from main.py.
    Fetches latest satellite image, analyzes it, and persists results to DB.
    """
    db_farm = db.query(DBFarm).filter(DBFarm.id == farm_id).first()
    if not db_farm:
        raise ValueError(f"Farm {farm_id} not found")

    coords = db_farm.coordinates
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    bbox = BBox(
        min_x=min(lons),
        min_y=min(lats),
        max_x=max(lons),
        max_y=max(lats),
    )

    logger.info(
        "Performing core analysis | farm_id=%d | bbox=%s | max_cloud=%.0f%%",
        farm_id,
        bbox,
        max_cloud_cover,
    )

    # ---- Fetch satellite image (blocking IO) -----------------------------
    # Note: Using run_in_threadpool because fetch_latest_image is synchronous
    image, meta = await run_in_threadpool(
        fetch_latest_image,
        bbox,
        max_cloud_cover,
        lookback_days,
        max_farm_cloud_cover,
    )

    # ---- Perform spectral analysis (CPU intensive) -----------------------
    result: AnalysisResult = await run_in_threadpool(analyze, image, bbox, coords)

    # ---- Build response object -------------------------------------------
    _severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_zones: list[StressZone] = sorted(
        result.stress_zones,
        key=lambda z: (_severity_order.get(z.severity, 9), -z.area_hectares),
    )

    stress_points = [
        StressPoint(
            coordinates=[z.lat, z.lon],
            severity=z.severity,
            area_hectares=z.area_hectares,
            pixel_count=z.pixel_count,
            possible_causes=z.possible_causes,
            metrics=StressMetrics(**z.metrics),
        )
        for z in sorted_zones
    ]

    fs = result.field_summary
    field_summary = FieldSummary(
        mean_ndvi=fs["mean_ndvi"],
        mean_ndwi=fs["mean_ndwi"],
        mean_ndre=fs["mean_ndre"],
        mean_evi=fs["mean_evi"],
        mean_ndmi=fs["mean_ndmi"],
        healthy_area_pct=fs["healthy_area_pct"],
        moderate_area_pct=fs["moderate_area_pct"],
        stressed_area_pct=fs["stressed_area_pct"],
        cloud_cover_pct=fs["cloud_cover_pct"],
        adaptive_stress_threshold=fs["adaptive_stress_threshold"],
    )

    response = AnalysisResponse(
        image_date=meta["acquisition_date"],
        bbox={
            "min_lon": bbox.min_x,
            "min_lat": bbox.min_y,
            "max_lon": bbox.max_x,
            "max_lat": bbox.max_y,
        },
        image_cloud_cover_pct=meta["cloud_cover_pct"],
        farm_cloud_cover_pct=meta["farm_cloud_cover_pct"],
        total_stress_zones=len(stress_points),
        stress_points=stress_points,
        field_summary=field_summary,
        warnings=result.warnings,
    )

    # ---- Persist results -------------------------------------------------
    # Update current status on farm for quick access
    db_farm.last_analysis = {
        "date": response.image_date,
        "status": "stress" if response.total_stress_zones > 0 else "healthy",
        "alerts": response.total_stress_zones,
        "healthy_pct": response.field_summary.healthy_area_pct,
        "stressed_pct": response.field_summary.stressed_area_pct,
        "primary_cause": response.stress_points[0].possible_causes[0] if response.stress_points else None,
        "stress_points": [p.model_dump() for p in response.stress_points]
    }
    
    # Save historical record only if values have changed
    last_record = db.query(DBAnalysisRecord).filter(
        DBAnalysisRecord.farm_id == farm_id
    ).order_by(DBAnalysisRecord.created_at.desc()).first()

    new_healthy = round(response.field_summary.healthy_area_pct, 2)
    new_stressed = round(response.field_summary.stressed_area_pct, 2)
    last_healthy = round(last_record.healthy_pct or 0, 2) if last_record else None
    last_stressed = round(last_record.stressed_pct or 0, 2) if last_record else None

    values_changed = (last_record is None) or (new_healthy != last_healthy) or (new_stressed != last_stressed)

    if values_changed:
        history_entry = DBAnalysisRecord(
            farm_id=farm_id,
            analysis_date=response.image_date,
            status="stress" if response.total_stress_zones > 0 else "healthy",
            healthy_pct=new_healthy,
            stressed_pct=new_stressed,
            stress_points=[p.model_dump() for p in response.stress_points]
        )
        db.add(history_entry)
        logger.info("New data detected — saving history record for farm %d", farm_id)
    else:
        logger.info("No data change — skipping duplicate history record for farm %d", farm_id)

    db.commit()
    logger.info("Persisted analysis results for farm %d", farm_id)

    return response
