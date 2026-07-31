"""models/route.py \u2014 Route, BusStop, RouteStop models."""
from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey, DateTime, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base




class Route(Base):
    __tablename__ = "routes"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    stops = relationship("BusStop", back_populates="route", order_by="BusStop.order_index")
    trips = relationship("Trip", back_populates="route")


class BusStop(Base):
    __tablename__ = "bus_stops"

    id          = Column(Integer, primary_key=True, index=True)
    route_id    = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    name        = Column(String(200), nullable=False)
    lat         = Column(Float, nullable=False)
    lon         = Column(Float, nullable=False)

    order_index = Column(Integer, nullable=False)  # position in route (0-based)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("route_id", "order_index", name="uq_route_stop_order"),)

    route = relationship("Route", back_populates="stops")
    users = relationship("User", back_populates="boarding_stop")
