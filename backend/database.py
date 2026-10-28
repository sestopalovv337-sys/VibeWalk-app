import os
import json
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Используем бесплатный облачный PostgreSQL (если задан в .env) или локальный SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vibewalk.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Place(Base):
    __tablename__ = "places"

    id = Column(Integer, primary_key=True, index=True)
    osm_id = Column(String, unique=True, index=True)
    name = Column(String, index=True, nullable=False)
    category = Column(String, index=True, nullable=False)
    budget_per_person = Column(Integer, default=1000)
    rating = Column(Float, default=4.5)
    tags_json = Column(Text, default="[]")
    address = Column(String, nullable=False)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    opening_hours = Column(String, nullable=True)
    is_partner = Column(Boolean, default=False)

    @property
    def tags(self):
        try:
            return json.loads(self.tags_json)
        except Exception:
            return []

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "budget_per_person": self.budget_per_person,
            "rating": self.rating,
            "tags": self.tags,
            "address": self.address,
            "lat": self.lat,
            "lon": self.lon,
            "opening_hours": self.opening_hours or "Ежедневно 10:00–23:00",
            "is_partner": self.is_partner
        }


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    place_name = Column(String, nullable=False)
    guest_name = Column(String, nullable=False)
    guests_count = Column(Integer, default=2)
    visit_time = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Poll(Base):
    __tablename__ = "polls"

    id = Column(String, primary_key=True, index=True)
    guests_count = Column(Integer, default=2)
    places_json = Column(Text, nullable=False)
    votes_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
