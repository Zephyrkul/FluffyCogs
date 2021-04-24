import asyncio
import contextvars
import functools
import hashlib
import itertools
import logging
import pathlib
import shutil
from datetime import datetime, timezone
from typing import Callable, Dict, Final, List, Literal, Set, TypedDict, TypeVar, Union

import discord
import youtube_dl
from redbot.core import Config, commands, modlog
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path
from redbot.core.utils import AsyncIter, deduplicate_iterables

# chunks >=2048 cause hashlib to release the GIL
BLOCKS: Final[int] = 128
HASHES: Final[str] = "HASHES"
LOG = logging.getLogger("red.fluffy.anticrashvid")
T = TypeVar("T")


# from itertools recipes <https://docs.python.org/3/library/itertools.html#itertools-recipes>
def all_equal(iterable):
    "Returns True if all the elements are equal to each other"
    g = itertools.groupby(iterable)
    return next(g, True) and not next(g, False)


# backport of 3.9's to_thread
async def to_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)  # type: ignore


class Settings(TypedDict):
    bypasslist: List[int]


class AntiCrashVid(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.init_custom(HASHES, 1)
        self.config.register_custom(HASHES, unsafe=None)
        self.config.register_guild(bypasslist=[])
        self.cache: Dict[int, Set[int]] = {}
        self.cog_ready = asyncio.Event()
        asyncio.ensure_future(self.initialize())

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        if requester == "user":
            LOG.info("Ignoring deletion request for user id %s", user_id)
            raise commands.RedUnhandledAPI()
        all_guilds = await self.config.all_guilds()
        settings: Settings
        async for _, settings in AsyncIter(all_guilds, steps=100):
            settings["bypasslist"].remove(user_id)

    async def cog_before_invoke(self, ctx) -> None:
        await self.cog_ready.wait()

    async def initialize(self):
        try:
            await modlog.register_casetype(
                name="malicious_video",
                default_setting=True,
                image="\N{TELEVISION}",
                case_str="Malicious video detected",
            )
        except RuntimeError:
            pass
        all_guilds: Dict[int, Settings] = await self.config.all_guilds()
        self.cache = {k: set(v["bypasslist"]) for k, v in all_guilds.items()}
        self.cog_ready.set()

    @commands.group()
    @commands.guildowner_or_permissions(administrator=True)
    async def anticrashvid(self, ctx: commands.GuildContext):
        """Manage anticrashvid's settings"""

    @anticrashvid.group(aliases=["whitelist"])
    async def bypasslist(self, ctx: commands.GuildContext):
        """
        Manage anticrashvid's bypasslist.

        Messages from members or roles in the bypasslist will not be checked for malicious videos.
        By default, server owners and full admins are exempt from anticrashvid's checks.
        """

    @bypasslist.command(require_var_positional=True)
    async def add(
        self, ctx: commands.GuildContext, *users_or_roles: Union[discord.Member, discord.Role, int]
    ):
        """Add users or roles to anticrashvid's bypasslist for this server."""
        ids = [getattr(i, "id", i) for i in users_or_roles]
        self.cache.setdefault(ctx.guild.id, set()).update(ids)
        async with self.config.guild(ctx.guild).bypasslist() as bypasslist:
            bypasslist[:] = deduplicate_iterables(bypasslist, ids)
        await ctx.tick()

    @bypasslist.command(require_var_positional=True)
    async def remove(
        self, ctx: commands.GuildContext, *users_or_roles: Union[discord.Member, discord.Role, int]
    ):
        """Remove users or roles from anticrashvid's bypasslist for this server."""
        ids = {getattr(i, "id", i) for i in users_or_roles}
        try:
            self.cache[ctx.guild.id] -= ids
        except KeyError:
            return await ctx.tick()
        async with self.config.guild(ctx.guild).bypasslist() as bypasslist:
            bypasslist[:] = [i for i in bypasslist if i not in ids]
        await ctx.tick()

    @bypasslist.command()
    async def clear(self, ctx: commands.GuildContext):
        """Clear anticrashvid's bypasslist for this server."""
        await self.config.guild(ctx.guild).clear()
        await ctx.tick()

    async def should_bypass(self, message: discord.Message) -> bool:
        # Note that bots and even the client itself are also checked by default
        if not message.guild:
            return True
        if LOG.isEnabledFor(logging.DEBUG):
            # for debugging purposes
            return False
        who = message.author
        assert isinstance(who, discord.Member)
        if message.guild.owner_id == who.id:
            return True
        if who.guild_permissions.administrator:
            return True
        if not LOG.isEnabledFor(logging.DEBUG) and await self.bot.is_owner(who):
            return True
        await self.cog_ready.wait()
        try:
            bypasslist = self.cache[message.guild.id]
        except KeyError:
            return False
        return not bypasslist.isdisjoint((who.id, *who._roles))  # type: ignore

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if await self.should_bypass(message):
            LOG.debug("Not checking message by author %s", message.author)
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
        if await self.should_bypass(message):
            LOG.debug("Not checking message by author %s", message.author)
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
        assert message.guild
        assert isinstance(message.channel, discord.TextChannel)
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
                reason=message.jump_url if not message_deleted else None,
            )
        except Exception:
            pass

    async def check_links(self, links: List[str], channel_id: int, message_id: int) -> List[bool]:
        directory = cog_data_path(self) / f"{channel_id}-{message_id}"
        try:
            if len(links) == 1:
                return [await self.check_link(links[0], directory)]
            return await asyncio.gather(
                *(self.check_link(link, directory) for link in links), return_exceptions=True
            )
        finally:
            shutil.rmtree(directory)

    async def check_link(self, link: str, path: pathlib.Path) -> bool:
        path.mkdir(parents=True)
        template = "%(title)s-%(id)s.%(ext)s"
        filename = template % await to_thread(
            self.dl_video, link, outtmpl=str(path / template), quiet=True, logger=LOG
        )
        video = path / filename
        digest = await to_thread(self.hexdigest, video)
        async with self.config.custom(HASHES, digest).unsafe.get_lock():
            LOG.debug("digest for video at link %r: %s", link, digest)
            if await self.config.custom(HASHES, digest).unsafe():
                LOG.debug("would remove message with link %r; cached digest @ %s", link, digest)
                return True
            else:
                LOG.debug("link %r not in digest cache", link)
            first, last = await asyncio.gather(
                self.get_probe(
                    "-loglevel",
                    "fatal",
                    "-i",
                    video,
                    "-vframes",
                    "1",
                    "-q:v",
                    "1",
                    path / "first.jpg",
                ),
                self.get_probe(
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
                    path / "last.jpg",
                ),
            )
            first_line, last_line = first.splitlines()[-1], last.splitlines()[-1]
            print(first_line, last_line, sep="\n")
            if first_line != last_line:
                LOG.debug(
                    "would remove message with link %r: ffprobe first and last frames have conflicting results",
                    link,
                )
                await self.config.custom(HASHES, digest).unsafe.set(True)
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
            out, _ = await process.communicate()
            if not all_equal(out.splitlines()):
                LOG.debug(
                    "would remove message with link %r: ffprobe frame dimentions are not constant",
                    link,
                )
                await self.config.custom(HASHES, digest).unsafe.set(True)
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
        _, err = await process.communicate()
        return err

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
