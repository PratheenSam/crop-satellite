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
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Response
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
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import Depends
from ai_diagnosis_diseases.predictor import predictor_instance
from models_db import (
    Farmer as DBFarmer, 
    Farm as DBFarm, 
    AnalysisRecord as DBAnalysisRecord,
    DiagnosisRecord as DBDiagnosisRecord,
    Disease as DBDisease,
    DiagnosticImage as DBDiagnosticImage
)

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

# Temporary Seeding Endpoint for Production (Render)
@app.post("/expert/seed", tags=["admin"])
async def trigger_seeding(key: str = Form(...), db: Session = Depends(get_db)):
    """ADMIN ONLY: Triggers the expert knowledge seeder remotely."""
    admin_key = os.getenv("SEED_ADMIN_KEY", "karsha_seed_2026")
    if key != admin_key: # Dynamic safety lock
        throw_auth_error()
    
    from expert_seeder import EXPERT_DATA
    try:
        updated = 0
        for entry in EXPERT_DATA:
            existing = db.query(DBDisease).filter(
                DBDisease.plant_name.ilike(f"%{entry['plant']}%"),
                DBDisease.disease_name.ilike(f"%{entry['disease']}%")
            ).first()
            if existing:
                existing.description, existing.prevention = entry['desc'], entry['prev']
                existing.symptoms, existing.chemical_remedy = entry['symp'], entry['chem']
                existing.organic_remedy = entry['org']
                existing.remedy = f"Chemical: {entry['chem']}\nOrganic: {entry['org']}"
            else:
                new_d = DBDisease(
                    plant_name=entry['plant'], disease_name=entry['disease'],
                    description=entry['desc'], prevention=entry['prev'],
                    symptoms=entry['symp'], chemical_remedy=entry['chem'],
                    organic_remedy=entry['org'],
                    remedy=f"Chemical: {entry['chem']}\nOrganic: {entry['org']}"
                )
                db.add(new_d)
            updated += 1
        db.commit()
        return {"status": "success", "entries_processed": updated}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

def throw_auth_error():
    raise HTTPException(status_code=401, detail="Unauthorized")

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
        village_name=farm.village_name,
        crop_category=farm.crop_category,
        crop_type=farm.crop_type,
        duration=farm.duration,
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
# AI Diagnosis Endpoints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AI Diagnosis Endpoints
# ---------------------------------------------------------------------------

@app.post("/diagnose", tags=["ai-diagnosis"])
async def diagnose_leaf(
    farmer_id: int = Form(...),
    crop_hint: str | None = Form(None),
    images: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """
    Accepts one or more images (e.g. Leaf, Stem, Root).
    Runs multi-shot consensus AI prediction and returns a unified diagnosis.
    """
    logger.info("Batch Diagnosis request from farmer %d (Hint: %s, Count: %d)", 
                farmer_id, crop_hint, len(images))
    
    db_farmer = db.query(DBFarmer).filter(DBFarmer.id == farmer_id).first()
    if not db_farmer:
        raise HTTPException(status_code=404, detail=f"Farmer {farmer_id} not found")

    try:
        image_contents = []
        first_image_id = None
        
        image_slots = []
        for img in images:
            content = await img.read()
            image_contents.append(content)
            
            # Extract slot from filename
            slot = img.filename.split('_')[0] if '_' in img.filename else "image"
            image_slots.append(slot)
            
            # SAVE TO NEON DB (Postgres)
            new_img = DBDiagnosticImage(
                farmer_id=farmer_id,
                image_data=content,
                filename=img.filename
            )
            db.add(new_img)
            db.flush() # Get ID before commit
            
            if first_image_id is None:
                first_image_id = new_img.id
        
        db.commit()
        
        # 2. RUN AI CONSENSUS PREDICTION
        cleaned_name, confidence, individuals, is_confused = await run_in_threadpool(
            predictor_instance.predict_consensus, image_contents, crop_hint=crop_hint
        )
        conf_float = float(confidence)
        
        # 4. Reliability Logic
        # > 85%: Highly Accurate
        # 65-85%: Moderate
        # < 65% or AI Confusion: Inconclusive

        # --- HARD PLANT MISMATCH CHECK ---
        # If we selected 'Cotton' but AI says 'Apple', it's a conflict.
        # We search the db early to check for this mismatch.
        # --- SMART DISEASE MAPPING ---
        # 1. Try Exact match or Substring match
        db_disease = db.query(DBDisease).filter(
            (DBDisease.disease_name.ilike(f"%{cleaned_name}%")) | 
            (func.lower(cleaned_name).contains(func.lower(DBDisease.disease_name)))
        ).first()

        # 2. If no match, try splitting the name (e.g. 'Banana Sigatoka' -> 'Sigatoka')
        # OR if it's a single word (e.g. 'Aphids')
        if not db_disease:
            parts = cleaned_name.split() if " " in cleaned_name else [cleaned_name]
            for part in parts:
                if len(part) > 3: 
                    db_disease = db.query(DBDisease).filter(DBDisease.disease_name.ilike(f"%{part}%")).first()
                    if db_disease: break

        if db_disease and crop_hint and crop_hint.lower() not in ["custom", "other"]:
            db_plant = db_disease.plant_name.lower()
            user_plant = crop_hint.lower()
            
            mismatch = True
            if db_plant == user_plant: mismatch = False
            if (db_plant == "corn" and user_plant == "maize") or (db_plant == "maize" and user_plant == "corn"): mismatch = False
            if (db_plant == "rice" and user_plant == "paddy") or (db_plant == "paddy" and user_plant == "rice"): mismatch = False
            
            if mismatch:
                # Still allow 'General' expert data for any crop
                if db_plant != "general":
                    is_confused = True
                    db_disease = None 

        status = "high" if conf_float >= 0.85 and not is_confused else \
                 ("moderate" if conf_float >= 0.65 and not is_confused else "inconclusive")
        
        # --- Individual Results for Tabs ---
        part_results = []
        for i, (label, conf, ind_confused) in enumerate(individuals):
            status_tag = "Low Signal"
            if conf > 0.70 and not ind_confused: status_tag = "Strong Signal"
            elif conf > 0.40 and not ind_confused: status_tag = "Clear Insight"
            elif conf > 0.15: status_tag = "Partial Match"
 
            part_results.append({
                "part": image_slots[i],
                "disease": label,
                "confidence": round(conf * 100, 1),
                "status": status_tag
            })
        
        # Assemble Response
        report_details = {
            "plant_name": (crop_hint.capitalize() if crop_hint else "Unknown") if is_confused else (db_disease.plant_name if db_disease else "Unknown"),
            "disease_name": db_disease.disease_name if db_disease else (cleaned_name if not is_confused else "Analysis Under Review"),
            "description": db_disease.description if db_disease else "Expert insights for this plant condition are being validated and will be updated in our database soon.",
            "symptoms": db_disease.symptoms if db_disease else "Visual symptoms for this specific case are currently being mapped by our experts. Updates coming soon.",
            "remedy": db_disease.remedy if db_disease else "Official expert remedies (Organic & Chemical) for this crop are being reviewed. Please consult a local expert while we update our guide.",
            "prevention": db_disease.prevention if db_disease else "General field hygiene and healthy management practices are recommended while we update our expert prevention guides.",
            "medicines": db_disease.medicines if db_disease else [],
            "local_names": db_disease.local_names if db_disease else {},
            "chemical_remedy": db_disease.chemical_remedy if db_disease else "Chemical intervention strategies for this specific crop will be updated soon.",
            "organic_remedy": db_disease.organic_remedy if db_disease else "Organic and natural solutions for this condition are under validation. Updates coming soon."
        }

        # Override title for 'moderate' results so it doesn't just say 'Diagnosis Needed'
        if status == "moderate":
            report_details["disease_name"] = f"Likely {report_details['disease_name']}"
            if not db_disease:
                report_details["description"] = "AI has identified potential symptoms. Please review the detailed photos in the tabs below for a final decision."

        # 6. Persistence
        new_record = DBDiagnosisRecord(
            farmer_id=farmer_id,
            image_id=first_image_id, 
            plant_name=report_details["plant_name"],
            disease_name=report_details["disease_name"],
            confidence=round(conf_float, 4),
            status=status,
            full_details=report_details,
            ai_model="Hybrid Expert (Reliability Enabled)"
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
 
        return {
            "status": status,
            "diagnosis": new_record,
            "display_name": cleaned_name,
            "confidence_pct": round(conf_float * 100, 1),
            "part_results": part_results,
            "details": report_details
        }

    except Exception as e:
        logger.exception("Diagnosis failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/diagnosis/images/{image_id}", tags=["ai-diagnosis"])
async def get_diagnosis_image(image_id: int, db: Session = Depends(get_db)):
    """Retrieves binary image data from Postgres for display in the app."""
    img_record = db.query(DBDiagnosticImage).filter(DBDiagnosticImage.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
    
    return Response(content=img_record.image_data, media_type="image/jpeg")

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

from fastapi.encoders import jsonable_encoder

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error for {request.url}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": exc.errors(), "body": exc.body}),
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception for %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred."},
    )
