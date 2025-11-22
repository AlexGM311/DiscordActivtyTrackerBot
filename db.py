from models import *
from sqlalchemy import create_engine, func, and_, desc, select
from sqlalchemy.orm import sessionmaker, Session
import os
from dotenv import load_dotenv
import inspect
load_dotenv()

engine = create_engine(os.getenv("DATABASE_URL"))
Base.metadata.create_all(engine)
s = sessionmaker(engine)
session: Session | None = s()


def get_user(user_handle: str) -> User | None:
    global session
    return session.query(User).filter_by(handle=user_handle).first()

def get_channel(channel_id: int) -> Channel | None:
    global session
    return session.query(Channel).filter_by(id=channel_id).first()

def add(obj: Base) -> None:
    global session
    stack = inspect.stack()
    caller_frame = stack[1]
    filename = caller_frame.filename
    line_number = caller_frame.lineno
    function_name = caller_frame.function
    print(f"Creation of a {obj.__repr__()} object at {filename} line {line_number} function {function_name}")
    session.add(obj)
    session.commit()

def rollback() -> None:
    session.rollback()

def get_active_users() -> list[User]:
    global session
    cte = text("""
    WITH latest_events AS (
        SELECT DISTINCT ON (public.user.handle) event.user, event."nextChannel"
        FROM event
        JOIN public.user ON event.user = public.user.handle
        ORDER BY public.user.handle ASC, event.timestamp DESC
    )
   SELECT latest_events.user
    FROM latest_events
    WHERE latest_events."nextChannel" IS NOT NULL
    """).columns(user=String)

    rows = session.execute(cte).fetchall()
    handles = [row[0] for row in rows]

    return session.query(User).filter(User.handle.in_(handles)).all()
