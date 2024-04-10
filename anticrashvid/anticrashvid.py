import asyncio
import contextvars
import functools
import hashlib
import logging
import math
import os
import pathlib
import shutil
from base64 import b85decode, b85encode
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Final, List, TypeVar

import discord
import youtube_dl
from redbot.core import Config, commands, modlog
from redbot.core.bot import Red
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.utils.chat_formatting import pagify

# chunks >=2048 cause hashlib to release the GIL
BLOCKS: Final[int] = 128
HASHES: Final[str] = "HASHES"
LOG = logging.getLogger("red.fluffy.anticrashvid")
T = TypeVar("T")

if TYPE_CHECKING:
    Hex = bytes
else:
    Hex = bytes.fromhex

if os.name == "nt":
    FFMPEG = "ffmpeg.exe"
    FFPROBE = "ffprobe.exe"
else:
    FFMPEG = "ffmpeg"
    FFPROBE = "ffprobe"


# backport of 3.9's to_thread
async def to_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)  # type: ignore


class VideoTooLong(Exception):
    """Exception raised when the video is too long. Not sure what else you were expecting."""


class EmptyOutputFile(Exception):
    """Exception raised when ffmpeg's output file is empty."""


# Credit for these fixes: https://www.reddit.com/r/discordapp/comments/mwsqm2/detect_discord_crash_videos_for_bot_developers/
class AntiCrashVid(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.init_custom(HASHES, 1)
        self.config.register_custom(HASHES, unsafe=None)

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass

    async def red_get_data_for_user(self, *, user_id):
        return {}

    async def cog_load(self) -> None:
        try:
            await modlog.register_casetype(
                name="malicious_video",
                default_setting=True,
                image="\N{TELEVISION}",
                case_str="Potentially malicious video detected",
            )
        except RuntimeError:
            pass
        await self.preload_hashes()

    async def preload_hashes(self, *, clear_past_hashes=False):
        async with self.config.custom(HASHES).all() as current_hashes:
            assert isinstance(current_hashes, dict)
            if clear_past_hashes:
                current_hashes.clear()
            await to_thread(self._insert_hashes, current_hashes)

    def _insert_hashes(self, hashes: dict):
        # b85 uses 5 ASCII chars to represent 4 bytes of data
        b85_digest_size = math.ceil(hashlib.sha512().digest_size / 4) * 5
        value = {"unsafe": True}
        with open(bundled_data_path(self) / "known_hashes", "rb") as file:
            while chunk := file.read(b85_digest_size):
                if len(chunk) == b85_digest_size:
                    hashes[b85decode(chunk).hex()] = value

    @commands.command(hidden=True)
    @commands.is_owner()
    async def export_hashes(self, ctx: commands.Context):
        """Exports known hashes as a base85-encoded block."""
        all_hashes = b"".join(
            b85encode(bytes.fromhex(k), pad=True)
            for k, v in (await self.config.custom(HASHES).all()).items()
            if v["unsafe"]
        )
        if all_hashes:
            return await ctx.send_interactive(
                pagify(all_hashes.decode("ascii"), shorten_by=10), box_lang=""
            )
        await ctx.send("No hashes to export.")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def clear_hashes(self, ctx: commands.Context):
        """
        Removes all hex digests from the cache.

        Known / pre-computed hashes will remain cached.
        """
        await self.preload_hashes(clear_past_hashes=True)
        await ctx.tick()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        debug = (message.author.id, self.bot.user.id) in [
            (215640856839979008, 256505473807679488),
            (281321316286726144, 346056290566406155),
        ]
        if not debug and (
            await self.bot.cog_disabled_in_guild(self, message.guild)
            or await self.bot.is_automod_immune(message)
        ):
            return
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
        if not any(await self.check_links(links, message.channel.id, message.id, debug=debug)):
            return
        await self.cry(message)

    @commands.Cog.listener()
    async def on_message_edit(self, _, message: discord.Message):
        if not message.guild:
            return
        debug = (message.author.id, self.bot.user.id) == (215640856839979008, 256505473807679488)
        if not debug and (
            await self.bot.cog_disabled_in_guild(self, message.guild)
            or await self.bot.is_automod_immune(message)
        ):
            return
        links = []
        for embed in message.embeds:
            if url := embed.video.url:
                assert isinstance(url, str)
                links.append(url)
        if not links:
            return
        if not any(await self.check_links(links, message.channel.id, message.id, debug=debug)):
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
            await modlog.create_case(
                bot=self.bot,
                guild=message.guild,
                # datetime.now because processing videos can take time
                created_at=datetime.now(timezone.utc),
                action_type="malicious_video",
                user=message.author,
                moderator=message.guild.me,
                channel=message.channel,
                reason=(
                    message.jump_url if not message_deleted else "Offending message was deleted."
                ),
            )
        except Exception:
            pass

    async def check_links(
        self, links: List[str], channel_id: int, message_id: int, *, debug: bool = False
    ) -> List[bool]:
        assert links
        directory = cog_data_path(self) / f"{channel_id}-{message_id}"
        try:
            if len(links) == 1:
                return [await self.check_link(links[0], directory, debug=debug)]
            return await asyncio.gather(
                *(
                    self.check_link(link, directory / str(i), debug=debug)
                    for i, link in enumerate(links)
                ),
                return_exceptions=True,
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    async def check_link(self, link: str, path: pathlib.Path, *, debug: bool = False) -> bool:
        path.mkdir(parents=True)
        template = "%(title)s-%(id)s.%(ext)s"
        try:
            filename = template % await to_thread(
                self.dl_video,
                link,
                outtmpl=os.path.join(str(path).replace("%", "%%"), template),
                quiet=True,
                logger=LOG,
                # anything less than "best" may download gifs instead,
                # which are seen as safe but are not actually safe
                format="best",
            )
        except VideoTooLong:
            LOG.info("Video at link %r was too long, and wasn't downloaded or probed.", link)
            return False
        video = path / filename
        video.with_suffix("").mkdir()
        digest = await to_thread(self.hexdigest, video)
        unsafe = self.config.custom(HASHES, digest).unsafe
        async with unsafe.get_lock():
            LOG.debug("digest for video at link %r: %s", link, digest)
            if await unsafe():
                LOG.debug("would remove message with link %r; cached digest @ %s", link, digest)
                if not debug:
                    return True
            else:
                LOG.debug("link %r not in digest cache", link)
            LOG.info(
                "Beginning first of three probes for link %r.\n"
                "If anticrashvid logs stop suddenly, then most likely your system has insufficient RAM for this cog.",
                link,
            )
            process = await asyncio.create_subprocess_exec(
                FFPROBE,
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
            prev = b""
            while line := await process.stdout.readline():
                if not (line := line.strip()):
                    continue
                if (prev and line != prev) or any(int(d) > 9999 for d in line.split(b",")):
                    process.terminate()
                    LOG.debug(
                        "would remove message with link %r: "
                        "ffprobe frame dimensions are not constant or are abnormally large\n\t%r\t%r",
                        link,
                        prev,
                        line,
                    )
                    await unsafe.set(True)
                    return True
                prev = line
            else:
                LOG.debug(
                    "ffprobe dimension scan for link %r complete, nothing abnormal found.", link
                )
            LOG.info("Beginning second probe for link %r.", link)
            try:
                first_line = await self.get_ffmpeg_probe(
                    "-loglevel",
                    "fatal",
                    "-i",
                    str(video),
                    "-vframes",
                    "1",
                    "-q:v",
                    "1",
                    path=video.with_suffix("") / "first.jpg",
                )
                LOG.info("Beginning third probe for link %r.", link)
                last_line = await self.get_ffmpeg_probe(
                    "-loglevel",
                    "fatal",
                    "-sseof",
                    "-3",
                    "-i",
                    str(video),
                    "-update",
                    "1",
                    "-q:v",
                    "1",
                    path=video.with_suffix("") / "last.jpg",
                )
                LOG.debug("first.jpg probe: %r\nlast.jpg probe: %r", first_line, last_line)
            except EmptyOutputFile:
                LOG.debug("Empty ffmpeg output.", exc_info=True)
            else:
                if first_line != last_line:
                    LOG.debug(
                        "would remove message with link %r: first/last frames have conflicting results",
                        link,
                    )
                    await unsafe.set(True)
                    return True
                else:
                    LOG.debug("link %r has consistent first/last ffmpeg probe results", link)
                del first_line, last_line
            LOG.info("Nothing abnormal found for link %r: video appears safe", link)

    @staticmethod
    async def get_ffmpeg_probe(*args: str, path: pathlib.Path) -> bytes:
        process = await asyncio.create_subprocess_exec(FFMPEG, *args, path)
        if code := await process.wait():
            raise RuntimeError(f"Process exited with exit code {code}")
        if not path.exists():
            raise RuntimeError(f"ffmpeg did not create a file at {path}")
        process = await asyncio.create_subprocess_exec(
            FFPROBE, "-i", path, stderr=asyncio.subprocess.PIPE
        )
        # only one pipe is used, so accessing it should™️ be safe
        assert process.stderr
        line = b""
        while next_line := await process.stderr.readline():
            if not next_line.isspace():
                line = next_line
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
            # don't download quite yet
            info = ytdl.extract_info(link, download=False)
            try:
                if info["duration"] > 60:
                    # 60s is arbitrary, but crashing videos are extremely unlikely to be very long
                    raise VideoTooLong
            except KeyError:
                pass
            return ytdl.extract_info(link)
