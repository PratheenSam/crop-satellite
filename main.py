"""
main.py
-------
FastAPI service that accepts farm coordinates, fetches the most recent
Sentinel-2 L2A image, and returns stress-zone analysis with spectral metrics.

Start:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Environment variables (copy .env.example → .env):
    SENTINELHUB_CLIENT_ID
    SENTINELHUB_CLIENT_SECRET
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from bbox import BBox

from image_analyzer import AnalysisResult, StressZone, analyze
from models import (
    AnalysisResponse,
    FarmRequest,
    FieldSummary,
    StressMetrics,
    StressPoint,
    FarmerCreate,
    FarmerUpdate,
    FarmCreate,
)
from sentinel_client import fetch_latest_image

from db import Base, engine, get_db
from models_db import Farmer as DBFarmer, Farm as DBFarm, AnalysisRecord as DBAnalysisRecord
from sqlalchemy.orm import Session
from fastapi import Depends

# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("crop-satellite")
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Crop-satellite service starting up. Creating tables...")
    Base.metadata.create_all(bind=engine)
    yield
    logger.info("Crop-satellite service shutting down.")


app = FastAPI(
    title="Crop Satellite Stress Analysis",
    description=(
        "Accepts farm polygon coordinates, fetches the most recent cloud-free "
        "Sentinel-2 image, and returns identified stress zones with NDVI, NDWI, "
        "NDRE, EVI, SAVI, MSAVI, NDMI, BSI, GNDVI, and CIre metrics together "
        "with likely agronomic causes."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development, allow all
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health check & Root
# ---------------------------------------------------------------------------

@app.api_route("/", methods=["GET", "HEAD"], tags=["meta"])
async def root() -> dict:
    return {"message": "Crop Satellite API is running. Use /health for status or /docs for API documentation."}

@app.api_route("/health", methods=["GET", "HEAD"], tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


from analysis_engine import perform_farm_analysis

# ---------------------------------------------------------------------------
# Primary analysis endpoint
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=AnalysisResponse, tags=["analysis"])
async def analyze_farm(payload: FarmRequest, db: Session = Depends(get_db)):
    """
    Fetch the most recent cloud-free satellite image for a farm's bounding box,
    calculate spectral indices (NDVI, NDWI, etc.), and detect stressed zones.
    """
    if not payload.farm_id:
        raise HTTPException(status_code=400, detail="farm_id is required for persistence and context")

    try:
        response = await perform_farm_analysis(
            db=db,
            farm_id=payload.farm_id,
            max_cloud_cover=payload.max_cloud_cover,
            lookback_days=payload.lookback_days,
            max_farm_cloud_cover=payload.max_farm_cloud_cover
        )
        return response
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Farmer & Farm Management (Persistence)
# ---------------------------------------------------------------------------

@app.get("/farmers", tags=["persistence"])
async def list_farmers(phone: str | None = None, db: Session = Depends(get_db)):
    query = db.query(DBFarmer)
    if phone:
        query = query.filter(DBFarmer.phone == phone)
    return query.all()

@app.post("/farmers", tags=["persistence"])
async def create_farmer(farmer: FarmerCreate, db: Session = Depends(get_db)):
    # Check if phone exists
    db_farmer = db.query(DBFarmer).filter(DBFarmer.phone == farmer.phone).first()
    if db_farmer:
        return db_farmer
    
    new_farmer = DBFarmer(
        name=farmer.name, 
        phone=farmer.phone, 
        village=farmer.village, 
        mandal=farmer.mandal, 
        district=farmer.district
    )
    db.add(new_farmer)
    db.commit()
    db.refresh(new_farmer)
    return new_farmer

@app.get("/farmers/{farmer_id}", tags=["persistence"])
async def get_farmer(farmer_id: int, db: Session = Depends(get_db)):
    db_farmer = db.query(DBFarmer).filter(DBFarmer.id == farmer_id).first()
    if not db_farmer:
        raise HTTPException(status_code=404, detail="Farmer not found")
    return db_farmer

@app.put("/farmers/{farmer_id}", tags=["persistence"])
async def update_farmer(farmer_id: int, data: FarmerUpdate, db: Session = Depends(get_db)):
    """Update any combination of farmer fields (name, phone, village, mandal, district)."""
    db_farmer = db.query(DBFarmer).filter(DBFarmer.id == farmer_id).first()
    if not db_farmer:
        raise HTTPException(status_code=404, detail="Farmer not found")
    update_data = data.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(db_farmer, field, value)
    db.commit()
    db.refresh(db_farmer)
    logger.info("Updated farmer %d: %s", farmer_id, list(update_data.keys()))
    return db_farmer

@app.post("/farms", tags=["persistence"])
async def create_farm(farm: FarmCreate, db: Session = Depends(get_db)):
    new_farm = DBFarm(
        farmer_id=farm.farmer_id,
        crop_type=farm.crop_type,
        sowing_date=farm.sowing_date,
        soil_type=farm.soil_type,
        irrigation_type=farm.irrigation_type,
        coordinates=farm.coordinates,
        area_hectares=farm.area_acres * 0.404686  # Convert to hectares
    )
    db.add(new_farm)
    db.commit()
    db.refresh(new_farm)
    return new_farm

@app.get("/farmers/{farmer_id}/farms", tags=["persistence"])
async def list_farms(farmer_id: int, db: Session = Depends(get_db)):
    return db.query(DBFarm).filter(DBFarm.farmer_id == farmer_id, DBFarm.is_active == 1).all()

@app.delete("/farms/{farm_id}", tags=["persistence"])
async def delete_farm(farm_id: int, db: Session = Depends(get_db)):
    logger.info("Soft-delete request for farm %d", farm_id)
    farm = db.query(DBFarm).filter(DBFarm.id == farm_id).first()
    if not farm:
        logger.warning("Farm %d not found for deletion", farm_id)
        raise HTTPException(status_code=404, detail="Farm not found")
    
    # Soft delete: mark as inactive instead of deleting from DB
    farm.is_active = 0
    db.commit()
    logger.info("Farm %d marked as inactive (soft deleted)", farm_id)
    return {"detail": "Farm deleted"}

@app.get("/farms/{farm_id}/history", tags=["persistence"])
async def list_farm_history(farm_id: int, db: Session = Depends(get_db)):
    """Retrieve all historical analysis records for a specific farm, sorted by date."""
    return db.query(DBAnalysisRecord).filter(DBAnalysisRecord.farm_id == farm_id).order_by(DBAnalysisRecord.created_at.desc()).all()

@app.patch("/farms/{farm_id}", tags=["persistence"])
async def update_farm(farm_id: int, data: dict, db: Session = Depends(get_db)):
    """Update any combination of farm fields (last_analysis, crop_type, etc)."""
    db_farm = db.query(DBFarm).filter(DBFarm.id == farm_id).first()
    if not db_farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    
    for field, value in data.items():
        if hasattr(db_farm, field):
            setattr(db_farm, field, value)
            
    db.commit()
    db.refresh(db_farm)
    logger.info("Updated farm %d: %s", farm_id, list(data.keys()))
    return db_farm

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error for {request.url}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception for %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred."},
    )
