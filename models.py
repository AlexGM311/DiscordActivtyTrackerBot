from typing import List, Optional

from discord import ChannelType, Member
from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Table, \
    text, Enum, BigInteger, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import datetime
import discord



class Base(DeclarativeBase):
    def __repr__(self):
        return "(Base)"
    pass


class Alias(Base):
    __tablename__ = 'alias'
    __table_args__ = (
        PrimaryKeyConstraint('name', 'handle', name='alias_pk'),
    )

    name: Mapped[str] = Column(String, nullable=False)
    handle: Mapped[str] = Column(String, ForeignKey('user.handle'), nullable=False)

    user: Mapped['User'] = relationship("User", back_populates="aliases")

    def __init__(self, user: 'User', name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.user = user

    def __eq__(self, other):
        if isinstance(other, str):
            return self.name == other
        return super().__eq__(other)

    def __repr__(self):
        return f"(Alias){self.handle}: {self.name}"


class Channel(Base):
    __tablename__ = 'channel'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='channel_pkey'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    is_afk: Mapped[bool] = mapped_column(Boolean)

    join_events: Mapped[List['Event']] = relationship('Event', foreign_keys='[Event.nextChannel]', back_populates='next_channel')
    leave_events: Mapped[List['Event']] = relationship('Event', foreign_keys='[Event.prevChannel]', back_populates='previous_channel')
    type: Mapped[str] = mapped_column(Enum('TextChannel', 'VoiceChannel', 'CategoryChannel', 'StageChannel', 'ForumChannel', name='channelTypes'))


    def __init__(self, channel: discord.abc.GuildChannel, **kwargs):
        super().__init__(**kwargs)
        self.id = channel.id
        self.name = channel.name
        mapping = {
            ChannelType.text: 'TextChannel',
            ChannelType.voice: 'VoiceChannel',
            ChannelType.category: 'CategoryChannel',
            ChannelType.stage_voice: 'StageChannel',
            ChannelType.forum: 'ForumChannel'
        }
        self.type = mapping[channel.type]
        self.is_afk = (channel == channel.guild.afk_channel)

    def __repr__(self):
        return f"(Channel){self.id}: {self.name}  afk-{self.is_afk} {self.type}"


class User(Base):
    __tablename__ = 'user'
    __table_args__ = (
        PrimaryKeyConstraint('handle', name='user_pkey'),
    )

    handle: Mapped[str] = mapped_column(String, primary_key=True)
    pfp: Mapped[Optional[str]] = mapped_column(String)
    is_bot: Mapped[Optional[bool]] = mapped_column(Boolean)

    events: Mapped[List['Event']] = relationship('Event', back_populates='user')
    aliases: Mapped[List['Alias']] = relationship('Alias', back_populates='user')

    def __init__(self, user: Member, **kwargs):
        super().__init__(**kwargs)
        self.handle = user.name
        self.pfp = user.avatar.url
        self.is_bot = user.bot

    @property
    def latest_event(self) -> 'Event':
        return max(self.events, key=lambda e: e.timestamp, default=None)

    def __repr__(self):
        return f"(User){self.handle}: bot-{self.is_bot} {self.pfp}"


class Event(Base):
    __tablename__ = 'event'
    __table_args__ = (
        ForeignKeyConstraint(['nextChannel'], ['channel.id'], name='event_nextChannel_channel_id_fk'),
        ForeignKeyConstraint(['prevChannel'], ['channel.id'], name='event_prevChannel_channel_id_fk'),
        ForeignKeyConstraint(['user'], ['user.handle'], name='event_user_user_handle_fk'),
        PrimaryKeyConstraint('id', name='event_pkey')
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prevChannel: Mapped[int] = mapped_column(BigInteger)
    nextChannel: Mapped[int] = mapped_column(BigInteger)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=text('now()'))
    user_: Mapped[Optional[str]] = mapped_column("user", String)

    """Channel that was joined"""
    next_channel: Mapped['Channel'] = relationship('Channel', foreign_keys=[nextChannel], back_populates='join_events')
    """Channel that was left"""
    previous_channel: Mapped['Channel'] = relationship('Channel', foreign_keys=[prevChannel], back_populates='leave_events')
    """User that triggered the event"""
    user: Mapped[Optional['User']] = relationship('User', back_populates='events')

    def __init__(self, previous_channel: Channel | None, next_channel: Channel | None, user: User | None, timestamp: Optional[datetime.datetime] = None, **kwargs):
        super().__init__(**kwargs)
        self.previous_channel = previous_channel
        self.next_channel = next_channel
        self.timestamp = timestamp if timestamp else datetime.datetime.now()
        self.user = user

    def __repr__(self) -> str:
        return f"(Event){self.user_}: {getattr(self.previous_channel, 'name', None)} -> {getattr(self.next_channel, 'name', None)} at {self.timestamp}"