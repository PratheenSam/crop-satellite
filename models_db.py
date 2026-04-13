from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from db import Base

class Farmer(Base):
    __tablename__ = "farmers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    phone = Column(String, unique=True, index=True)
    village = Column(String)
    mandal = Column(String)
    district = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    farms = relationship("Farm", back_populates="owner")

class Farm(Base):
    __tablename__ = "farms"

    id = Column(Integer, primary_key=True, index=True)
    farmer_id = Column(Integer, ForeignKey("farmers.id"))
    crop_type = Column(String)
    sowing_date = Column(String)  # Storing as string for simplicity in this flow
    soil_type = Column(String)
    irrigation_type = Column(String)
    # Storing coordinates as a JSON list of [lat, lon] pairs
    coordinates = Column(JSON)
    area_hectares = Column(Float)
    last_analysis = Column(JSON, nullable=True)  # New: Persist analysis results
    is_active = Column(Integer, default=1)       # New: Soft delete support (1=active, 0=deleted)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("Farmer", back_populates="farms")
    analysis_history = relationship("AnalysisRecord", back_populates="farm", cascade="all, delete-orphan")

class AnalysisRecord(Base):
    __tablename__ = "analysis_history"

    id = Column(Integer, primary_key=True, index=True)
    farm_id = Column(Integer, ForeignKey("farms.id"))
    analysis_date = Column(String)  # Date of the satellite image
    status = Column(String)         # 'healthy' or 'stress'
    healthy_pct = Column(Float)
    stressed_pct = Column(Float)
    stress_points = Column(JSON)    # Full list of stress points for this date
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    farm = relationship("Farm", back_populates="analysis_history")
