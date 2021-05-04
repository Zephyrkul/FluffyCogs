import asyncio
import contextvars
import functools
import hashlib
import logging
import os
import pathlib
import shutil
from datetime import datetime, timezone
from typing import Callable, Final, List, TypeVar

import discord
import youtube_dl
from redbot.core import Config, commands, modlog
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

# chunks >=2048 cause hashlib to release the GIL
BLOCKS: Final[int] = 128
HASHES: Final[str] = "HASHES"
LOG = logging.getLogger("red.fluffy.anticrashvid")
T = TypeVar("T")

# backport of 3.9's to_thread
async def to_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)  # type: ignore


# Credit for these fixes: https://www.reddit.com/r/discordapp/comments/mwsqm2/detect_discord_crash_videos_for_bot_developers/
class AntiCrashVid(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.init_custom(HASHES, 1)
        self.config.register_custom(HASHES, unsafe=None)
        self.case_ready = asyncio.Event()
        asyncio.ensure_future(self.initialize())

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass

    async def red_get_data_for_user(self, *, user_id):
        return {}

    async def initialize(self):
        try:
            await modlog.register_casetype(
                name="malicious_video",
                default_setting=True,
                image="\N{TELEVISION}",
                case_str="Potentially malicious video detected",
            )
        except RuntimeError:
            pass
        self.case_ready.set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not LOG.isEnabledFor(logging.DEBUG):
            if await self.bot.is_automod_immune(message):
                LOG.debug("Not checking message by author %s: is automod immune", message.author)
            elif await self.bot.cog_disabled_in_guild(self, message.guild):
                LOG.debug(
                    "Not checking message by author %s: cog is disabled in guild %s",
                    message.author,
                    message.guild,
                )
            return
        assert message.guild
        links = []
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("video/"):
                links.append(attachment.proxy_url)
        for embed in message.embeds:
            if url := embed.video.url:
                assert isinstance(url, str)
                links.append(url)
        if not links:
            return
        if not any(await self.check_links(links, message.channel.id, message.id)):
            return
        await self.cry(message)

    @commands.Cog.listener()
    async def on_message_edit(self, _, message: discord.Message):
        if not LOG.isEnabledFor(logging.DEBUG):
            if await self.bot.is_automod_immune(message):
                LOG.debug("Not checking message by author %s: is automod immune", message.author)
            elif await self.bot.cog_disabled_in_guild(self, message.guild):
                LOG.debug(
                    "Not checking message by author %s: cog is disabled in guild %s",
                    message.author,
                    message.guild,
                )
            return

        assert message.guild
        links = []
        for embed in message.embeds:
            if url := embed.video.url:
                assert isinstance(url, str)
                links.append(url)
        if not links:
            return
        if not any(await self.check_links(links, message.channel.id, message.id)):
            return
        await self.cry(message)

    async def cry(self, message: discord.Message):
        assert message.guild and isinstance(message.channel, discord.TextChannel)
        message_deleted = False
        try:
            if (
                message.author == message.guild.me
                or message.channel.permissions_for(message.guild.me).manage_messages
            ):
                await message.delete()
                message_deleted = True
        except discord.HTTPException:
            pass
        try:
            await self.case_ready.wait()
            await modlog.create_case(
                bot=self.bot,
                guild=message.guild,
                # datetime.now because processing videos can take time
                created_at=datetime.now(timezone.utc),
                action_type="malicious_video",
                user=message.author,
                moderator=message.guild.me,
                channel=message.channel,
                reason=message.jump_url
                if not message_deleted
                else "Offending message was deleted.",
            )
        except Exception:
            pass

    async def check_links(self, links: List[str], channel_id: int, message_id: int) -> List[bool]:
        assert links
        directory = cog_data_path(self) / f"{channel_id}-{message_id}"
        try:
            if len(links) == 1:
                return [await self.check_link(links[0], directory)]
            return await asyncio.gather(
                *(self.check_link(link, directory / str(i)) for i, link in enumerate(links)),
                return_exceptions=True,
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    async def check_link(self, link: str, path: pathlib.Path) -> bool:
        path.mkdir(parents=True)
        template = "%(title)s-%(id)s.%(ext)s"
        filename = template % await to_thread(
            self.dl_video,
            link,
            outtmpl=os.path.join(str(path).replace("%", "%%"), template),
            quiet=True,
            logger=LOG,
        )
        video = path / filename
        video.with_suffix("").mkdir()
        digest = await to_thread(self.hexdigest, video)
        unsafe = self.config.custom(HASHES, digest).unsafe
        async with unsafe.get_lock():
            LOG.debug("digest for video at link %r: %s", link, digest)
            if await unsafe():
                LOG.debug("would remove message with link %r; cached digest @ %s", link, digest)
                return True
            else:
                LOG.debug("link %r not in digest cache", link)
            first_line = await self.get_probe(
                "-loglevel",
                "fatal",
                "-i",
                video,
                "-vframes",
                "1",
                "-q:v",
                "1",
                video.with_suffix("") / "first.jpg",
            )
            last_line = await self.get_probe(
                "-loglevel",
                "fatal",
                "-sseof",
                "-3",
                "-i",
                video,
                "-update",
                "1",
                "-q:v",
                "1",
                video.with_suffix("") / "last.jpg",
            )
            LOG.debug("first probe: %r\nlast probe: %r", first_line, last_line)
            if first_line != last_line:
                LOG.debug(
                    "would remove message with link %r: ffprobe first and last frames have conflicting results",
                    link,
                )
                await unsafe.set(True)
                return True
            else:
                LOG.debug("link %r has consistent first/last ffprobe results", link)
            del first_line, last_line
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "frame=width,height",
                "-select_streams",
                "v",
                "-of",
                "csv=p=0",
                video,
                stdout=asyncio.subprocess.PIPE,
            )
            # only one pipe is used, so accessing it should™️ be safe
            assert process.stdout
            first_line = await process.stdout.readline()
            while line := await process.stdout.readline():
                if line != first_line:
                    process.terminate()
                    LOG.debug(
                        "would remove message with link %r: ffprobe frame dimentions are not constant",
                        link,
                    )
                    await unsafe.set(True)
                    return True
            LOG.debug("link %r looks safe", link)
            return False

    @staticmethod
    async def get_probe(*args) -> bytes:
        process = await asyncio.create_subprocess_exec("ffmpeg", *args)
        if code := await process.wait():
            raise RuntimeError(f"Process exited with exit code {code}")
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-i", args[-1], stderr=asyncio.subprocess.PIPE
        )
        # only one pipe is used, so accessing it should™️ be safe
        assert process.stderr
        line = b""
        while next_line := await process.stderr.readline():
            line = next_line
        if code := await process.wait():
            raise RuntimeError(f"Process exited with exit code {code}")
        return line

    @staticmethod
    def hexdigest(path) -> str:
        hasher = hashlib.sha512()
        block = BLOCKS * hasher.block_size
        with open(path, "rb") as file:
            while chunk := file.read(block):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def dl_video(link: str, /, **options) -> dict:
        with youtube_dl.YoutubeDL(options) as ytdl:
            return ytdl.extract_info(link)
