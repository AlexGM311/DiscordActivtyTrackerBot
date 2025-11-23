import asyncio
import datetime
from os import getenv

import dotenv
import os
from datetime import datetime as dt, timedelta
from asyncio import sleep
import logging
import uvicorn
from discord import Member, VoiceState
from db import *
from models import *
import atexit

dotenv.load_dotenv()


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(levelname)s - %(message)s",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": f"{datetime.datetime.now().strftime('%d-%m-%Y_%H-%M-%S')}.log",
            "formatter": "default",
        }
    },
    "loggers": {
        "uvicorn": {"handlers": ["file"], "level": "INFO"},
        "uvicorn.error": {"handlers": ["file"], "level": "INFO"},
        "uvicorn.access": {"handlers": ["file"], "level": "INFO"},
    },
}

logging.basicConfig(
    filename=LOG_CONFIG["handlers"]["file"]["filename"],
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
client = discord.Client(intents=intents)
TOKEN_DISCORD = os.getenv("TOKEN_DISCORD")
active_users = 0

@client.event
async def on_ready():
    global active_users
    await client.change_presence(status=discord.Status.invisible)
    active_users = 0
    logging.info(f'✅ Бот запущен как {client.user}')
    for guild in client.guilds:
        for channel in guild.channels:
            channel_db = get_channel(channel.id)
            if not channel_db:
                channel_db = Channel(channel)
                add(channel_db)
            if type(channel) is discord.VoiceChannel:
                for member in channel.members:
                    if not member.bot and channel != guild.afk_channel:
                        active_users += 1
                    user = get_user(member.name)
                    if not user:
                        user = User(member)
                        add(user)
                    if not user.aliases or member.display_name not in user.aliases:
                        add(Alias(user, member.display_name))
                    if not user.pfp == member.avatar.url:
                        user.pfp = member.avatar.url
                    join = Event(None, channel_db, user)
                    add(join)
    logging.info(f"👶 Активно {active_users} пользователей")


@client.event
async def on_voice_state_update(member: Member, before: VoiceState, after: VoiceState):
    if before.channel == after.channel:
        return
    global active_users
    previous_channel: Channel | None = None
    if before.channel:
        previous_channel = get_channel(before.channel.id)
        if not previous_channel:
            previous_channel = Channel(before.channel)
            add(previous_channel)
    next_channel: Channel | None = None
    if after.channel:
        next_channel = get_channel(after.channel.id)
        if not next_channel:
            next_channel = Channel(after.channel)
    user: User | None = get_user(member.name)
    if not user:
        user = User(member)
        add(user)
    if not user.aliases or member.display_name not in user.aliases:
        add(Alias(user, member.display_name))
    if not user.pfp == member.avatar.url:
        user.pfp = member.avatar.url
    add(Event(previous_channel, next_channel, user))
    if member.bot:
        return
    if next_channel and (not previous_channel or previous_channel.is_afk) and not next_channel.is_afk:
        if not member.bot:
            active_users += 1
    elif not after.channel or (before.channel and after.channel == after.channel.guild.afk_channel) and before.channel != before.channel.guild.afk_channel:
        if not member.bot:
            active_users -= 1
            active_users = max(0, active_users)
    logging.info(f"👶 Активно {active_users} пользователей")


@atexit.register
def close_all_sessions() -> None:
    unclosed_users: list[User] = get_active_users()
    for user in unclosed_users:
        add(Event(user.latest_event.next_channel, None, user))


async def start_uvicorn():
    cfg = uvicorn.Config(
        "api:app",
        host=getenv("HOST"),
        port=int(getenv("PORT")),
        log_level="debug",
        reload=False,
        access_log=True,
        log_config=LOG_CONFIG
    )
    server = uvicorn.Server(cfg)
    await server.serve()


async def main():
    await asyncio.gather(
        # client.start(TOKEN_DISCORD),
        start_uvicorn()
    )

if __name__ == "__main__":
    asyncio.run(main())
