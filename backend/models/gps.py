"""models/gps.py \u2014 GPS log model."""
from sqlalchemy import (
    Column, BigInteger, Float, String, DateTime, Integer, ForeignKey
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base




class GpsLog(Base):
    __tablename__ = "gps_logs"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    server_time = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)

    speed       = Column(Float, nullable=True)   # km/h from QGPSLOC if available
    event       = Column(String(30), nullable=True)  # POWER_ON | POWER_OFF | NULL
    trip_id     = Column(Integer, ForeignKey("trips.id"), nullable=True)

    trip = relationship("Trip", back_populates="gps_logs")
