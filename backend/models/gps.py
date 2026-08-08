"""models/gps.py \u2014 GPS log model."""
from sqlalchemy import (
    Column, BigInteger, Float, String, DateTime, Integer, ForeignKey
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base




class GpsLog(Base):
    __tablename__ = "gps_realtime"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now(), index=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    event       = Column(String(30), nullable=True)

    @property
    def created_at(self):
        return self.server_time

    @property
    def gps_time(self):
        return self.server_time
