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

## 📂 Key Files
- `main.py`: The entry point for all API calls (`/analyze`, `/farmers`, `/farms`).
- `image_analyzer.py`: The "Math Lab" where we calculate plant health from satellite pixels.
- `sentinel_client.py`: The bridge to get images from outer space.
- `models_db.py`: Defines how farmers and fields are saved in our database.

## 📝 A Quick Note for the Human
This backend is designed to be **robust but light**. It uses a "Thread Pool" for satellite requests so it doesn't freeze up when multiple farmers are checking their fields at once. Keep your terminal open while the farmers are using the app!

---
*Powering the future of agriculture, one pixel at a time.*
