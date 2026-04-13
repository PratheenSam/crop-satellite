# Crop Satellite Backend (FastAPI) 🛰️📡

Welcome to the brain of the Karsha platform! This backend handles the heavy lifting: fetching satellite images, analyzing crop stress, and saving farmer data.

## 🧠 How it Works
1. **Request**: The mobile app sends a request with field coordinates (latitude/longitude).
2. **Fetch**: We talk to **Sentinel Hub** to get the latest cloud-free satellite image for that exact spot.
3. **Analyze**: We calculate multiple "Spectral Indices" (like NDVI for greenness, NDWI for water, etc.).
4. **Identify**: We find "Stress Zones" where the plants aren't doing well and suggest possible reasons (Water stress, Pest, or Low Nutrients).

## 🛠️ Technical Stack
- **Framework**: FastAPI (Python) - Super fast and modern.
- **Database**: SQLite (local file) - Keeps things simple and portable.
- **Satellite Provider**: Sentinel-2 L2A (European Space Agency via Sentinel Hub).
- **Public Access**: `localtunnel` (npx) – allows your phone to reach your laptop from anywhere.

## ⚙️ Setup & Running
1. **Environment**: You need a `.env` file with your `SENTINELHUB_CLIENT_ID` and `SECRET`.
2. **Start Backend**:
   ```bash
   source venv/bin/activate
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```
3. **Start Tunnel**:
   ```bash
   npx localtunnel --port 8000
   ```
   *Note: Use the URL provided (e.g., tangy-colts-send.loca.lt) in your mobile app's configuration.*

## � Background Automation: 6-Hour Scheduler
The backend includes a dedicated background worker (`scheduler.py`) that ensures all fields are kept up-to-date without manual interaction.

### When does it update?
The scheduler runs on a **fixed 6-hour interval**. If you start the scheduler at **12:00 AM**, it will automatically trigger scans at:
*   **06:00 AM**
*   **12:00 PM (Noon)**
*   **06:00 PM**
*   **12:00 AM (Midnight)**

### How it works:
1.  **Immediate Scan**: Upon starting `scheduler.py`, it performs an immediate scan of all fields.
2.  **Safety Filter**: It only scans fields where **`is_active = 1`** (active fields).
3.  **Neon Sync**: All results are saved directly to your **Neon PostgreSQL** database.

## �📂 Backend File Breakdown

### 🛠️ Core Services
- **`main.py`**: The API entry point. Handles all mobile app requests (Login, Register, List Farms). It now uses the centralized analysis engine and supports **Soft Deletes**.
- **`scheduler.py`**: The background worker. It runs every 6 hours to automatically scan all active fields without farmer intervention.
- **`analysis_engine.py`**: The shared engine that contains the logic for fetching satellite data, running stress analysis, and saving results to the database.

### 🛰️ Satellite & Analysis
- **`sentinel_client.py`**: Communicates with the Sentinel Hub API to fetch raw satellite bands (Red, Blue, Green, NIR, etc.).
- **`image_analyzer.py`**: The "Math Lab." Calculates NDVI, NDWI, and other indices to detect crop stress and identify potential causes (Pests, Water, etc.).
- **`bbox.py`**: Utility to handle "Bounding Boxes" for correctly framing satellite images based on farm coordinates.

### 🗄️ Database & Models
- **`db.py`**: Configures the connection to your **Neon PostgreSQL** database.
- **`models_db.py`**: Defines the database schema (Farmers, Farms, Analysis History). Now includes the `is_active` field.
- **`models.py`**: Contains Pydantic models used to validate data sent to and from the mobile app.
- **`migrate_db.py`**: A utility script to initialize or update database tables.

### ⚙️ Configuration
- **`.env`**: Stores sensitive API credentials and your Neon Database URL.
- **`requirements.txt`**: Lists all Python libraries needed (FastAPI, SQLAlchemy, APScheduler, etc.).

## 📝 A Quick Note for the Human
This backend is designed to be **robust but light**. It uses a "Thread Pool" for satellite requests so it doesn't freeze up when multiple farmers are checking their fields at once. Keep your terminal open while the farmers are using the app!

---
*Powering the future of agriculture, one pixel at a time.*
# crop-satellite
