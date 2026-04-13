import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from db import SessionLocal
from models_db import Farm as DBFarm
from analysis_engine import perform_farm_analysis

# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | SCHEDULER | %(message)s"
)
logger = logging.getLogger("crop-scheduler")
# ---------------------------------------------------------------------------

async def auto_update_job():
    """
    Main background job that iterates through all farms and runs analysis.
    """
    logger.info("Starting scheduled harvest scan for all fields...")
    db: Session = SessionLocal()
    
    try:
        farms = db.query(DBFarm).filter(DBFarm.is_active == 1).all()
        logger.info("Found %d active farms to process.", len(farms))
        
        for farm in farms:
            try:
                logger.info("Processing farm %d (Crop: %s)...", farm.id, farm.crop_type)
                # We use default cloud settings for auto-updates
                await perform_farm_analysis(
                    db=db,
                    farm_id=farm.id,
                    max_cloud_cover=20.0,
                    lookback_days=30,
                    max_farm_cloud_cover=50.0
                )
                # Small delay to prevent hitting API rate limits too hard
                await asyncio.sleep(2) 
            except Exception as e:
                logger.error("Failed to auto-update farm %d: %s", farm.id, str(e))
                db.rollback() # Ensure one failure doesn't leave session in bad state
                continue
                
        logger.info("Scheduled scan complete.")
        
    except Exception as e:
        logger.error("Global scheduler job error: %s", str(e))
    finally:
        db.close()

async def main():
    scheduler = AsyncIOScheduler()
    
    # Schedule the job to run every 6 hours
    # For testing/demo purposes, you could change this to minutes=1
    scheduler.add_job(auto_update_job, 'interval', hours=6, id='harvest_scan_job')
    
    logger.info("Scheduler initialized. Interval: 6 hours.")
    
    # Run once immediately on startup
    asyncio.create_task(auto_update_job())
    
    scheduler.start()
    
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
