import logging
import secrets
import traceback
from datetime import timedelta
from itertools import count
from math import ceil
import hashlib
from os import getenv
import fastapi

# Configure logging
logger = logging.getLogger("app")
logging.getLogger("app").setLevel(logging.DEBUG)


from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from db import *
import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel, RootModel
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/dbname")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

active_tokens = set()

# Pydantic Models (matching TypeScript interfaces)
class AliasModel(BaseModel):
    name: str


class UserModel(BaseModel):
    id: str
    handle: str
    pfp: Optional[str] = None
    isBot: bool
    aliases: List[AliasModel]
    lastSeen: Optional[datetime.datetime] = None
    totalEvents: int
    channels: List[str]


class ActivityData(RootModel[Dict[str, Dict[str, bool]]]):
    pass

class TimeSlotData(BaseModel):
    time: str
    hour: int
    minute: int
    averageUsers: float
    peakUsers: int


# FastAPI app initialization
app = FastAPI(title="User Activity API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def require_auth(x_auth: str = Header(None)):
    if x_auth not in active_tokens:
        raise HTTPException(401, "not authorized")


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Helper functions
def get_user_channels(db, user_handle: str) -> List[str]:
    """Get unique channels a user has been in"""
    result = db.query(Channel.name).distinct().join(
        Event, or_(Event.prevChannel == Channel.id, Event.nextChannel == Channel.id)
    ).filter(Event.user_ == user_handle).all()
    return [r[0] for r in result]


def get_last_seen(db, user_handle: str) -> Optional[datetime.datetime]:
    """Get the last event timestamp for a user"""
    result = db.query(func.max(Event.timestamp)).filter(Event.user_ == user_handle).scalar()
    return result


def get_total_events(db, user_handle: str) -> int:
    """Get total number of events for a user"""
    return db.query(func.count(Event.id)).filter(Event.user_ == user_handle).scalar()


# API Endpoints
@app.get("/api/users", response_model=List[UserModel])
async def get_users(auth=fastapi.Depends(require_auth)):
    db = next(get_db())
    try:
        users = db.query(User).all()
        user_models = []

        for user in users:
            aliases = [AliasModel(name=alias.name) for alias in user.aliases]
            channels = get_user_channels(db, user.handle)
            last_seen = get_last_seen(db, user.handle)
            total_events = get_total_events(db, user.handle)

            user_model = UserModel(
                id=user.handle,
                handle=user.handle,
                pfp=user.pfp,
                isBot=user.is_bot,
                aliases=aliases,
                lastSeen=last_seen,
                totalEvents=total_events,
                channels=channels
            )
            user_models.append(user_model)

        return user_models
    except Exception:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()


@app.get("/api/activity/{year}/{month}", response_model=ActivityData)
async def get_activity_data_for_month(year: int, month: int, auth=fastapi.Depends(require_auth)):
    db = next(get_db())
    try:
        # Validate month/year
        if not (1 <= month <= 12):
            raise HTTPException(status_code=400, detail="Invalid month")

        # Calculate date range
        start_date = datetime.date(year, month, 1)
        if month == 12:
            end_date = datetime.date(year + 1, 1, 1)
        else:
            end_date = datetime.date(year, month + 1, 1)

        # Get all dates in the month
        dates = []
        current_date = start_date
        while current_date < end_date:
            dates.append(current_date)
            current_date += datetime.timedelta(days=1)

        # Get all users
        users = db.query(User).all()
        user_handles = [user.handle for user in users]
        active = set()

        # Build result dictionary
        result = {}
        for date in dates:
            date_str = date.isoformat()
            result[date_str] = {}

            # For each user, check if they had any events on this date
            for handle in user_handles:
                event_count = db.query(func.count(Event.id)).filter(
                    Event.user_ == handle,
                    func.date(Event.timestamp) == date
                ).scalar()
                if event_count > 0:
                    active.add(handle)
                result[date_str][handle] = event_count > 0
        inactive = set(user_handles) - active
        for date in dates:
            date_str = date.isoformat()
            for name in inactive:
                result[date_str].pop(name)
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()



def floor_to_10_minutes(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 10) * 10, second=0, microsecond=0)


def ceil_to_10_minutes(dt: datetime) -> datetime:
    m = ceil(dt.minute / 10) * 10
    if m == 60:
        return dt.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    return dt.replace(minute=m, second=0, microsecond=0)


@app.get("/api/activity/graph", response_model=List[TimeSlotData])
async def get_activity_graph_data(
    start: Optional[str] = Query(
        None,
        description="Inclusive start of the sampling window (ISO 8601 format, e.g. 2024-03-01T00:00:00)",
        example="2024-03-01T00:00:00"
    ),
    end: Optional[str] = Query(
        None,
        description="Inclusive end of the sampling window (ISO 8601 format, e.g. 2024-03-10T23:59:59)",
        example="2024-03-10T23:59:59"),
    auth=fastapi.Depends(require_auth)
) -> List[TimeSlotData]:
    start_dt = None
    end_dt = None

    if start:
        try:
            # Handle ISO format with and without timezone
            start_dt = datetime.datetime.fromisoformat(start)
        except ValueError as e:
            logger.error(f"Invalid start date format: {start} - {e}")
            raise HTTPException(status_code=400, detail="Invalid start date format. Use ISO 8601 format.")
    if end:
        try:
            end_dt = datetime.datetime.fromisoformat(end)
        except ValueError as e:
            logger.error(f"Invalid end date format: {end} - {e}")
            raise HTTPException(status_code=400, detail="Invalid end date format. Use ISO 8601 format.")

    try:
        # 1. Fetch all events + their channels\
        db = next(get_db())

        if start_dt and end_dt:
        # noinspection PyTypeChecker
            events: List[Event] = db.query(Event).where(Event.id>3941,
                                                        Event.timestamp >= start_dt,
                                                        Event.timestamp <= end_dt).all()
        elif start_dt:
        # noinspection PyTypeChecker
            events: List[Event] = db.query(Event).where(Event.id>3941,
                                                        Event.timestamp >= start_dt).all()
        elif end_dt:
        # noinspection PyTypeChecker
            events: List[Event] = db.query(Event).where(Event.id>3941,
                                                        Event.timestamp <= end_dt).all()
        else:
        # noinspection PyTypeChecker
            events: List[Event] = db.query(Event).where(Event.id>3941).all()

        if not events:
            return []

        # 2. Load all channels in memory for AFK lookup
        channels = {ch.id: ch for ch in db.query(Channel).all()}

        def is_afk(channel_id: int | None) -> bool:
            if channel_id is None:
                return False
            ch = channels.get(channel_id)
            return ch.is_afk if ch else False

        # 3. Build "sweep line" event list: (timestamp, +1/-1)
        line_events = []  # (timestamp, delta)

        for e in events:
            if e.user is not None and (e.prevChannel == -1 or e.nextChannel == -1):
                continue

            prev = e.prevChannel
            nxt = e.nextChannel

            prev_afk = is_afk(prev)
            next_afk = is_afk(nxt)

            # OPEN: from None or AFK → non-AFK
            if (prev is None or prev_afk) and (nxt is not None and not next_afk):
                line_events.append((e.timestamp, +1))
                continue

            # CLOSE: from non-AFK → None or AFK
            if (prev is not None and not prev_afk) and (nxt is None or next_afk):
                line_events.append((e.timestamp, -1))
                continue

            # Ignore: non-AFK → non-AFK
            # Do nothing

        if not line_events:
            return []

        # 4. Sort all sweep events
        line_events.sort(key=lambda x: x[0])

        # 5. Determine the time span for buckets
        start_time = floor_to_10_minutes(line_events[0][0])
        end_time = ceil_to_10_minutes(line_events[-1][0])

        def datetime_to_bucket(dt):
            return (dt.hour * 60 + dt.minute) // 10

        # 7. Sweep through events while iterating buckets
        result: List[TimeSlotData] = []
        active_users = 0
        num_events = len(line_events)
        final_buckets = [[] for _ in range(144)]
        bucket_start = start_time
        bucket_end = bucket_start + datetime.timedelta(minutes=10)
        event_i = 0

        while bucket_end <= end_time:
            while event_i < num_events and line_events[event_i][0] <= bucket_end:
                _, delta = line_events[event_i]
                active_users += delta
                event_i += 1

            final_buckets[datetime_to_bucket(bucket_start)].append(active_users)
            bucket_start += timedelta(minutes=10)
            bucket_end += timedelta(minutes=10)
        for i in range(144):
            result.append(
                TimeSlotData(
                    time=f"{i//6:02d}:{(i%6)*10:02d}",
                    hour=i % 24,
                    minute=(i % 6) * 10,
                    averageUsers=sum(final_buckets[i])/len(final_buckets[i]),
                    peakUsers=max(final_buckets[i])
                ) if len(final_buckets[i]) > 0  else
                TimeSlotData(
                    time=f"{i//6:02d}:{(i%6)*10:02d}",
                    hour=i % 24,
                    minute=(i % 6) * 10,
                    averageUsers=0,
                    peakUsers=0
                )
            )
        return result
    except Exception:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error")

logger.info("help")

# Health check endpoint
@app.get("/api/health")
async def health_check():
    logger.info("healthy")
    return {"status": "healthy"}

@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    password = data.get("password")
    logger.info(f"Trying to access, p: {password}, md5: {hashlib.md5(password.encode()).hexdigest()}")
    if hashlib.md5(password.encode()).hexdigest() == getenv("MD5_PASSWORD"):
        token = secrets.token_hex(32)
        active_tokens.add(token)
        return {"token": token}
    else:
        raise HTTPException(401, "wrong password")


# Run with: uvicorn api:app --host 0.0.0.0 --port 8001 --reload --log-level debug --access-log