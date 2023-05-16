import asyncio
import bisect
import heapq
import re
import time
from datetime import datetime
from enum import Flag, auto
from functools import reduce
from html import unescape
from io import BytesIO
from itertools import chain, islice
from operator import or_
from typing import (
    Callable,
    Dict,
    Generator,
    Generic,
    Iterable,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
    Union,
    get_args,
)
from xml.etree import ElementTree as etree

import discord
import httpx
import sans
from proxyembed import ProxyEmbed
from redbot.core import Config, commands, version_info as red_version
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, escape, humanize_list, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, close_menu, menu

_T = TypeVar("_T")


# from https://docs.python.org/3/library/itertools.html#itertools-recipes
def batched(iterable: Iterable[_T], n: int) -> Generator[Tuple[_T, ...], None, None]:
    "Batch data into tuples of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def controls(data: Iterable[str], *, paged: bool):
    async def save(ctx: commands.Context, *args):
        with BytesIO(
            "\n".join(",".join(batch) for batch in batched(data, 8)).encode("utf-8")
        ) as bio:
            await ctx.send(file=discord.File(bio, filename=f"{ctx.invoked_with}.txt"))
        return await menu(ctx, *args[:-1])

    if paged:
        return {**DEFAULT_CONTROLS, "\N{FLOPPY DISK}": save}
    return {"\N{CROSS MARK}": close_menu, "\N{FLOPPY DISK}": save}


class Options(Flag):
    @classmethod
    async def convert(cls, ctx, argument: str) -> "Options":
        argument = argument.upper().rstrip("S")
        try:
            return cls[argument]
        except KeyError as ke:
            raise commands.BadArgument() from ke

    @classmethod
    def collapse(cls, *args: "Options", default: Union["Options", int] = 0):
        if not args:
            return cls(default)
        return cls(reduce(or_, args))


class Nation(Options):
    ALL = -1
    NONE = 0


class Region(Options):
    ALL = -1
    NONE = 0


class WA(Options):
    ALL = -1
    NONE = 0
    TEXT = auto()
    VOTE = auto()
    NATION = auto()
    DELEGATE = auto()


CARD_COLORS = {
    "legendary": 0xFFD700,
    "epic": 0xDB9E1C,
    "rare": 0x008EC1,
    "uncommon": 0x00AA4C,
    "common": 0x7E7E7E,
}
LINK_RE = re.compile(
    r'(?i)["<]?\b(?:https?:\/\/)?(?:www\.)?nationstates\.net\/(?:(nation|region)=)?([-\w\s]+)\b[">]?'
)
WA_RE = re.compile(r"(?i)\b(UN|GA|SC)R?#(\d+)\b")


class Link(str, Generic[_T]):
    @classmethod
    async def convert(cls, ctx, link: str):
        match = LINK_RE.match(link)
        if not match:
            return "_".join(link.strip('"<>').casefold().split())
        if (match.group(1) or "nation").casefold() == get_args(cls)[0].__name__.casefold():
            return "_".join(match.group(2).casefold().split())
        raise commands.BadArgument()


class NationStates(commands.Cog):
    # __________ DATA API __________

    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    # __________ INIT __________

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(agent=None)
        self.db_cache: Dict[str, Dict[Literal["dbid"], str]] = {}
        self.config.init_custom("NATION", 1)
        self.config.register_custom("NATION", dbid=None)
        self.cog_ready = asyncio.Event()
        self.client = sans.AsyncClient()
        asyncio.create_task(self.initialize())

    async def initialize(self):
        agent = await self.config.agent()
        if not agent:
            await self.bot.wait_until_red_ready()
            if not self.bot.owner_ids:
                # always False but forces owner_ids to be filled
                await self.bot.is_owner(discord.Object(id=0))  # type: ignore
            owner_ids = self.bot.owner_ids
            # only make the user_info request if necessary
            agent = humanize_list(
                [str(self.bot.get_user(id) or await self.bot.fetch_user(id)) for id in owner_ids]  # type: ignore
            )
        sans.set_agent(f"{agent} Red-DiscordBot/{red_version}", _force=True)  # type: ignore
        self.db_cache = await self.config.custom("NATION").all()
        self.cog_ready.set()

    async def cog_unload(self):
        await self.client.aclose()

    async def cog_check(self, ctx: commands.Context):
        # this will also cause `[p]agent` to be blocked but this is intended
        if ctx.cog is not self:
            return True
        when = sans._state.lock.deferred
        if when:
            raise commands.CommandOnCooldown(
                commands.Cooldown(50, 30), when - time.monotonic(), commands.BucketType.default
            )
        await ctx.defer()
        await self.cog_ready.wait()
        return True

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        original = getattr(error, "original", None)
        if original:
            if isinstance(original, httpx.TimeoutException):
                return await ctx.send("Request timed out.")
            if isinstance(original, sans.HTTPStatusError):
                return await ctx.send(f"{original.response.status_code}: {''.join(original.args)}")
        return await ctx.bot.on_command_error(ctx, error, unhandled_by_cog=True)  # type: ignore

    # __________ UTILS __________

    async def _get_as_xml(self, *args: str, **kwargs: str):
        request = sans.World(*args, **kwargs)
        response = await self.client.get(request)
        response.raise_for_status()
        return response.xml

    @staticmethod
    def _find_text_and_assert(
        root: etree.Element, find: str, as_: Callable[[str], _T] = str
    ) -> _T:
        return as_(root.find(find).text)  # type: ignore

    @staticmethod
    def _illion(num: float):
        illion = ("million", "billion", "trillion", "quadrillion")
        num = float(num)
        index = 0
        while num >= 1000:
            index += 1
            num /= 1000
        return "{} {}".format(round(num, 3), illion[index])

    @staticmethod
    def _is_zday(snowflake: discord.abc.Snowflake):
        epoch = discord.utils.snowflake_time(snowflake.id)
        month, day = epoch.month, epoch.day
        return (month == 10 and day >= 29) or (month == 11 and day <= 6)

    # __________ LISTENERS __________

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot:
            return
        await self.cog_ready.wait()
        ctx: commands.Context = await self.bot.get_context(message)
        if ctx.valid:
            return
        if not await self.wa.can_run(ctx):
            return
        index = ["un", "ga", "sc"]
        for match in WA_RE.finditer(message.content):
            council = index.index(match.group(1).lower())
            res_id = match.group(2)
            if council == 0:
                await ctx.send(
                    f"https://www.nationstates.net/page=WA_past_resolution/id={res_id}/un=1"
                )
                continue
            ctx.invoked_with = match.group(1).lower()
            await ctx.invoke(self.wa, int(res_id), WA.NONE)

    # __________ STANDARD __________

    @commands.command(cooldown_after_parsing=True)  # type: ignore
    @commands.cooldown(2, 3600)
    @commands.is_owner()
    async def agent(self, ctx: commands.Context, *, agent: str):
        """
        Sets the user agent.

        Recommendations: https://www.nationstates.net/pages/api.html#terms
        Defaults to your username#hash
        """
        full_agent = f"{agent} Red-DiscordBot/{red_version}"
        sans.set_agent(full_agent, _force=True)  # type: ignore
        await self.config.agent.set(agent)
        await ctx.send(f"Agent set: {full_agent}")

    @commands.hybrid_command()
    async def nation(self, ctx: commands.Context, *, nation: Link[Nation]):
        """Retrieves general info about a specified NationStates nation"""
        try:
            root = await self._get_as_xml(
                "census category dbid "
                "demonym2plural flag founded freedom "
                "fullname influence lastlogin "
                "name population region wa zombie",
                nation=nation,
                mode="score",
                scale="65 66",
            )
        except sans.NotFound:
            embed = ProxyEmbed(
                title=nation.replace("_", " ").title(),
                url=f"https://www.nationstates.net/page=boneyard?nation={nation}",
                description="This nation does not exist.",
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            embed.set_thumbnail(url="http://i.imgur.com/Pp1zO19.png")
            return await embed.send_to(ctx)
        n_id = root.get("id")
        assert n_id is not None
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": self._find_text_and_assert(root, "DBID")}
            await self.config.custom("NATION", n_id).dbid.set(
                self._find_text_and_assert(root, "DBID")
            )
        endo = self._find_text_and_assert(root, "CENSUS/SCALE[@id='66']/SCORE", float)
        if endo == 1:
            endo = "{:.0f} endorsement".format(endo)
        else:
            endo = "{:.0f} endorsements".format(endo)
        founded = self._find_text_and_assert(root, "FOUNDED")
        if founded == "0":
            founded = "in Antiquity"
        is_zday = self._is_zday(ctx.message)
        embed = ProxyEmbed(
            title=self._find_text_and_assert(root, "FULLNAME"),
            url="https://www.nationstates.net/nation={}".format(root.get("id")),
            description="[{}](https://www.nationstates.net/region={})"
            " | {} {} | Founded {}".format(
                self._find_text_and_assert(root, "REGION"),
                "_".join(self._find_text_and_assert(root, "REGION").lower().split()),
                self._illion(self._find_text_and_assert(root, "POPULATION", int)),
                self._find_text_and_assert(root, "DEMONYM2PLURAL"),
                founded,
            ),
            timestamp=datetime.utcfromtimestamp(
                self._find_text_and_assert(root, "LASTLOGIN", int)
            ),
            colour=0x8BBC21 if is_zday else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        embed.set_thumbnail(url=self._find_text_and_assert(root, "FLAG"))
        embed.add_field(
            name=self._find_text_and_assert(root, "CATEGORY"),
            value="{}\t|\t{}\t|\t{}".format(
                self._find_text_and_assert(root, "FREEDOM/CIVILRIGHTS"),
                self._find_text_and_assert(root, "FREEDOM/ECONOMY"),
                self._find_text_and_assert(root, "FREEDOM/POLITICALFREEDOM"),
            ),
            inline=False,
        )
        embed.add_field(
            name=self._find_text_and_assert(root, "UNSTATUS"),
            value="{} | {:.0f} influence ({})".format(
                endo,
                float(self._find_text_and_assert(root, "CENSUS/SCALE[@id='65']/SCORE")),
                self._find_text_and_assert(root, "INFLUENCE"),
            ),
            inline=False,
        )
        if is_zday:
            embed.add_field(
                name="{}{}".format(
                    (self._find_text_and_assert(root, "ZOMBIE/ZACTION") or "No Action").title(),
                    " (Unintended)"
                    if self._find_text_and_assert(root, "ZOMBIE/ZACTIONINTENDED")
                    else "",
                ),
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/SURVIVORS", int)),
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/ZOMBIES", int)),
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/DEAD", int)),
                ),
                inline=False,
            )
        embed.add_field(
            name="Cards",
            value=(
                "[{0}'s Deck](https://www.nationstates.net/page=deck/nation={1})\t|"
                "\t[{0}'s Card](https://www.nationstates.net/page=deck/card={2})".format(
                    self._find_text_and_assert(root, "NAME"),
                    n_id,
                    self._find_text_and_assert(root, "DBID"),
                )
            ),
        )
        embed.set_footer(text="Last Active")
        await embed.send_to(ctx)

    @commands.hybrid_command()
    async def region(self, ctx: commands.Context, *, region: Link[Region]):
        """Retrieves general info about a specified NationStates region"""
        try:
            root = await self._get_as_xml(
                "delegate delegateauth delegatevotes flag founded founder "
                "governor lastupdate name numnations officers power tags zombie",
                region=region,
            )
        except sans.NotFound:
            embed = ProxyEmbed(
                title=region.replace("_", " ").title(), description="This region does not exist."
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            return await embed.send_to(ctx)
        if self._find_text_and_assert(root, "DELEGATE") == "0":
            delvalue = "\u200b"
        else:
            endo = self._find_text_and_assert(root, "DELEGATEVOTES", int) - 1
            if endo == 1:
                endo = "{:.0f} endorsement".format(endo)
            else:
                endo = "{:.0f} endorsements".format(endo)
            delvalue = "[{}](https://www.nationstates.net/nation={}) | {}".format(
                self._find_text_and_assert(root, "DELEGATE").replace("_", " ").title(),
                self._find_text_and_assert(root, "DELEGATE"),
                endo,
            )
        if "X" in self._find_text_and_assert(root, "DELEGATEAUTH"):
            delheader = "Delegate"
        else:
            delheader = "Delegate (Non-Executive)"
        tags = {t.text for t in root.iterfind("TAGS/TAG")}
        # Note: This logic will mark TRR as "Sinker", not "Catcher"
        # The reason for this can be found here: https://forum.nationstates.net/viewtopic.php?f=15&t=533753
        # TODO: Change TRR's classification once the "Catcher" tag is renamed
        major_tag = next(
            filter(
                tags.__contains__, ("Frontier", "Feeder", "Restorer", "Sinker", "Governorless")
            ),
            "Stronghold",
        )
        founded = self._find_text_and_assert(root, "FOUNDED")
        if founded == "0":
            founded = "in Antiquity"
        execvalue = []
        founder = self._find_text_and_assert(root, "FOUNDER")
        if founder != "0":
            if "Founderless" in tags:
                url = "https://www.nationstates.net/page=boneyard?nation="
            else:
                url = "https://www.nationstates.net/nation="
            execvalue.append(
                "Founder: [{}]({}{}){}".format(
                    founder.replace("_", " ").title(),
                    url,
                    founder,
                    " (Ceased to Exist)" if ("Founderless" in tags) else "",
                )
            )
        governor = self._find_text_and_assert(root, "GOVERNOR")
        if governor != "0":
            if major_tag == "Governorless":
                url = "https://www.nationstates.net/page=boneyard?nation="
            else:
                url = "https://www.nationstates.net/nation="
            execvalue.append(
                "Governor: [{}]({}{}){}".format(
                    governor.replace("_", " ").title(),
                    url,
                    governor,
                    " (Ceased to Exist)" if (major_tag == "Governorless") else "",
                )
            )
        officers = root.find("OFFICERS")
        assert officers is not None
        for officer in officers:
            if "S" in self._find_text_and_assert(officer, "AUTHORITY"):
                successor = self._find_text_and_assert(officer, "NATION")
                execvalue.append(
                    "Successor: [{}](https://www.nationstates.net/nation={})".format(
                        successor.replace("_", " ").title(), successor
                    )
                )
        fash = "Fascist" in tags and "Anti-Fascist" not in tags  # why do people hoard tags...
        name = "{}{}".format(
            "\N{LOCK} " if "Password" in tags else "", self._find_text_and_assert(root, "NAME")
        )
        warning = (
            "\n**```css\n\N{HEAVY EXCLAMATION MARK SYMBOL} Region Tagged as Fascist \N{HEAVY EXCLAMATION MARK SYMBOL}\n```**"
            if fash
            else ""
        )

        numnations = self._find_text_and_assert(root, "NUMNATIONS", int)
        description = "[{} nation{}](https://www.nationstates.net/region={}/page=list_nations) | Founded {} | Power: {}{}".format(
            numnations,
            "" if numnations == 1 else "s",
            root.get("id"),
            founded,
            self._find_text_and_assert(root, "POWER"),
            warning,
        )
        is_zday = self._is_zday(ctx.message)
        embed = ProxyEmbed(
            title=name,
            url="https://www.nationstates.net/region={}".format(root.get("id")),
            description=description,
            timestamp=datetime.utcfromtimestamp(
                self._find_text_and_assert(root, "LASTUPDATE", int)
            ),
            colour=0x000001 if fash else 0x8BBC21 if is_zday else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        flag = root.find("FLAG").text  # type: ignore
        if flag:
            embed.set_thumbnail(url=flag)
        embed.add_field(name=delheader, value=delvalue, inline=True)
        embed.add_field(
            name=major_tag,
            value="\n".join(execvalue) if execvalue else "\u200b",
            inline=True,
        )
        if is_zday:
            embed.add_field(
                name="Zombies",
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/SURVIVORS", int)),
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/ZOMBIES", int)),
                    self._illion(self._find_text_and_assert(root, "ZOMBIE/DEAD", int)),
                ),
                inline=False,
            )
        embed.set_footer(text="Last Updated")
        await embed.send_to(ctx)

    # __________ CARDS __________

    @commands.command(usage="[season] <nation>")
    async def card(
        self,
        ctx: commands.Context,
        season: Optional[int] = 3,
        *,
        nation: Optional[Union[int, Link[Nation]]] = None,
    ):
        """
        Retrieves general info about the specified card.

        If a number is provided, the bot will look for the card with that ID.
        Otherwise, the bot will look for the specified nation's card.

        If you want to find a nation that has a numerical name,
        use a link or "quotes" to specify that it is a name, and not an ID.
        A season must be specified if this is the case.
        """
        if season is not None and nation is None:
            season, nation = 3, season
        if isinstance(nation, str) and nation not in self.db_cache:
            try:
                root = await self._get_as_xml("dbid", nation=nation)
            except sans.NotFound:
                return await ctx.send(
                    f"Nation {nation!r} does not exist. "
                    "Please provide its card ID instead, and I'll remember it for next time."
                )
            n_id, nation = root.get("id"), self._find_text_and_assert(root, "DBID")
            self.db_cache[n_id] = {"dbid": nation}
            await self.config.custom("NATION", n_id).dbid.set(nation)
        else:
            n_id, nation = None, self.db_cache.get(nation, {}).get("dbid", nation)
        root = await self._get_as_xml("card info markets", cardid=nation, season=season)
        if not len(root):
            if n_id:
                return await ctx.send(f"No such S{season} card for nation {n_id!r}.")
            return await ctx.send(f"No such S{season} card for ID {nation!r}.")
        n_id = self._find_text_and_assert(root, "NAME").casefold().replace(" ", "_")
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": nation}
            await self.config.custom("NATION", n_id).dbid.set(nation)
        embed = ProxyEmbed(
            title=f"The {self._find_text_and_assert(root, 'TYPE')} of {self._find_text_and_assert(root, 'NAME')}",
            url=f"https://www.nationstates.net/page=deck/card={nation}/season={season}",
            colour=CARD_COLORS.get(self._find_text_and_assert(root, "CATEGORY"), 0),
        )
        embed.set_author(name=self._find_text_and_assert(root, "CATEGORY").title())
        embed.set_thumbnail(
            url=f"https://www.nationstates.net/images/cards/s{season}/{self._find_text_and_assert(root, 'FLAG')}"
        )
        embed.add_field(
            name="Market Value (estimated)",
            value=box(self._find_text_and_assert(root, "MARKET_VALUE"), lang="swift"),
            inline=False,
        )
        sellers: List[Tuple[float, str]] = []
        buyers: List[Tuple[float, str]] = []
        for market in root.find("MARKETS"):
            if self._find_text_and_assert(market, "TYPE") == "bid":
                # negative price to reverse sorting
                bisect.insort(
                    buyers,
                    (
                        -self._find_text_and_assert(market, "PRICE", float),
                        self._find_text_and_assert(market, "NATION").replace("_", " ").title(),
                    ),
                )
            elif self._find_text_and_assert(market, "TYPE") == "ask":
                bisect.insort(
                    sellers,
                    (
                        self._find_text_and_assert(market, "PRICE", float),
                        self._find_text_and_assert(market, "NATION").replace("_", " ").title(),
                    ),
                )
        if not any((buyers, sellers)):
            return await embed.send_to(ctx)
        max_listed = 5
        max_len = max(len(buyers), len(sellers))
        max_len = min(max_len, max_listed + 1)
        for is_buyers, arr in enumerate((sellers, buyers)):
            pad = "\n\u200b" * max(0, max_len - len(arr))
            if not arr:
                embed.add_field(name="Buyers" if is_buyers else "Sellers", value=box(pad))
                embed.overwrites.set_field_at(
                    is_buyers + 1,
                    name=embed.fields[is_buyers + 1].name,
                    value=box("\u200b", lang="swift"),
                )
                continue
            tarr = [
                f"{-j[0]:.02f}\xa0{j[1]}" if is_buyers else f"{j[1]}\xa0{j[0]:.02f}"
                for j in arr[:max_listed]
            ]
            if len(arr) > max_listed:
                num = len(arr) - max_listed
                tarr.append(
                    f"+ {num} more {'bid' if is_buyers else 'ask'}{'' if num == 1 else 's'}\N{HORIZONTAL ELLIPSIS}"
                )
            raw_text = "\n".join(tarr)
            if not is_buyers:
                width = max(map(len, tarr))
                for i, t in enumerate(tarr):
                    tarr[i] = t.rjust(width)
            text = "\n".join(tarr) + pad
            embed.add_field(
                name="Buyers" if is_buyers else "Sellers", value=box(text, lang="swift")
            )
            embed.overwrites.set_field_at(
                is_buyers + 1,
                name=embed.fields[is_buyers + 1].name,
                value=box(raw_text, lang="swift"),
            )
        await embed.send_to(ctx)

    @commands.command()
    async def deck(self, ctx: commands.Context, *, nation: Union[int, Link[Nation]]):
        """Retrieves general info about the specified nation's deck."""
        is_id = isinstance(nation, int)
        if is_id:
            root = await self._get_as_xml("cards info", nationid=str(nation))
        else:
            root = await self._get_as_xml("cards info", nationname=nation)
        if not len(root.find("INFO")):
            if is_id:
                return await ctx.send(f"No such deck for ID {nation}.")
            return await ctx.send(f"No such deck for nation {nation!r}.")
        n_id = self._find_text_and_assert(root, "INFO/NAME")
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": self._find_text_and_assert(root, "INFO/ID")}
            await self.config.custom("NATION", n_id).dbid.set(
                self._find_text_and_assert(root, "INFO/ID")
            )
        embed = ProxyEmbed(
            title=n_id.replace("_", " ").title(),
            url=f"https://www.nationstates.net/page=deck/nation={n_id}",
            description=f"{self._find_text_and_assert(root, 'INFO/NUM_CARDS')} cards",
            colour=await ctx.embed_colour(),
            timestamp=datetime.utcfromtimestamp(
                self._find_text_and_assert(root, "INFO/LAST_VALUED", int)
            ),
        )
        embed.add_field(name="Bank", value=self._find_text_and_assert(root, "INFO/BANK"))
        embed.add_field(
            name="Deck Value",
            value=f"[{self._find_text_and_assert(root, 'INFO/DECK_VALUE')}]"
            f"(https://www.nationstates.net/nation={n_id}/detail=trend/censusid=86)"
            f"\nRanked #{self._find_text_and_assert(root, 'INFO/RANK')} worldwide, "
            f"#{self._find_text_and_assert(root, 'INFO/REGION_RANK')} regionally.",
            inline=False,
        )
        embed.set_footer(text="Last Valued")
        await embed.send_to(ctx)

    # __________ ASSEMBLY __________

    @commands.command(aliases=["ga", "sc"])
    async def wa(self, ctx: commands.Context, resolution_id: Optional[int] = None, *options: WA):
        """
        Retrieves general info about World Assembly resolutions.

        Defaults to the General Assembly. Use [p]sc to get info about the Security Council.
        If no resolution ID is provided, the current at-vote resolution is used.
        Valid options:
            text - The resolution's text
            votes - The total votes for and against
            nations - The total nations for and against
            delegates - The top ten Delegates for and against
        """
        option = WA.collapse(*options, default=0)
        if resolution_id and option & (WA.NATION | WA.DELEGATE):
            return await ctx.send(
                "The Nations and Delegates options are not available for past resolutions."
            )
        is_sc = ctx.invoked_with == "sc"
        shards = ["resolution"]
        request = {"wa": "2" if is_sc else "1"}
        if option & WA.DELEGATE:
            shards.append("delvotes")
        if resolution_id:
            request["id"] = str(resolution_id)
        else:
            shards.append("lastresolution")
        root = await self._get_as_xml(*shards, **request)
        if not len(root.find("RESOLUTION")):
            out = (
                unescape(self._find_text_and_assert(root, "LASTRESOLUTION"))
                .replace("<strong>", "**")
                .replace("</strong>", "**")
            )
            try:
                out = "{}[{}](https://www.nationstates.net{}){}".format(
                    out[: out.index("<a")],
                    out[out.index('">') + 2 : out.index("</a")],
                    out[out.index('="') + 2 : out.index('">')],
                    out[out.index("</a>") + 4 :],
                )
            except ValueError:
                pass
            embed = ProxyEmbed(
                title="Last Resolution", description=out, colour=await ctx.embed_colour()
            )
            embed.set_thumbnail(
                url="https://www.nationstates.net/images/{}.jpg".format("sc" if is_sc else "ga")
            )
            return await embed.send_to(ctx)
        root = root.find("RESOLUTION")
        assert root
        img = {
            "Commendation": "https://i.imgur.com/peX3xUf.png",
            "Condemnation": "https://i.imgur.com/IK3itYt.png",
            "Liberation": "https://i.imgur.com/O7jOkyr.png",
            "Injunction": "https://i.imgur.com/5Tq1yfa.png",
            "Declaration": "https://www.nationstates.net/images/sc.jpg",
        }.get(
            self._find_text_and_assert(root, "CATEGORY"), "https://nationstates.net/images/ga.jpg"
        )
        if option & WA.TEXT:
            description = "**Category: {}**\n\n{}".format(
                self._find_text_and_assert(root, "CATEGORY"),
                escape(self._find_text_and_assert(root, "DESC"), formatting=True),
            )
            short = next(
                pagify(
                    description,
                    delims=("\n", " ", "]"),
                    escape_mass_mentions=False,
                    page_length=2047,
                    priority=True,
                )
            )
            if len(short) < len(description):
                description = short + "\N{HORIZONTAL ELLIPSIS}"
        else:
            description = "Category: {}".format(self._find_text_and_assert(root, "CATEGORY"))
        if resolution_id:
            impl = self._find_text_and_assert(root, "IMPLEMENTED", int)
        else:
            # mobile embeds can't handle the FUTURE
            impl = self._find_text_and_assert(
                root, "PROMOTED", int
            )  # + (4 * 24 * 60 * 60)  # 4 Days
        embed = ProxyEmbed(
            title=self._find_text_and_assert(root, "NAME"),
            url="https://www.nationstates.net/page={}".format("sc" if is_sc else "ga")
            if not resolution_id
            else "https://www.nationstates.net/page=WA_past_resolution/id={}/council={}".format(
                resolution_id, "2" if is_sc else "1"
            ),
            description=description,
            timestamp=datetime.utcfromtimestamp(impl),
            colour=await ctx.embed_colour(),
        )
        try:
            authroot = await self._get_as_xml(
                "fullname flag", nation=self._find_text_and_assert(root, "PROPOSED_BY")
            )
        except sans.NotFound:
            embed.set_author(
                name=self._find_text_and_assert(root, "PROPOSED_BY").replace("_", " ").title(),
                url="https://www.nationstates.net/page=boneyard?nation={}".format(
                    self._find_text_and_assert(root, "PROPOSED_BY")
                ),
                icon_url="http://i.imgur.com/Pp1zO19.png",
            )
        else:
            embed.set_author(
                name=self._find_text_and_assert(authroot, "FULLNAME"),
                url="https://www.nationstates.net/nation={}".format(
                    self._find_text_and_assert(root, "PROPOSED_BY")
                ),
                icon_url=self._find_text_and_assert(authroot, "FLAG"),
            )
        embed.set_thumbnail(url=img)
        if option & WA.DELEGATE:
            for_del_votes = heapq.nlargest(
                10,
                root.iterfind("DELVOTES_FOR/DELEGATE"),
                key=lambda el: self._find_text_and_assert(el, "VOTES", int),
            )
            against_del_votes = heapq.nlargest(
                10,
                root.iterfind("DELVOTES_AGAINST/DELEGATE"),
                key=lambda el: self._find_text_and_assert(el, "VOTES", int),
            )
            if for_del_votes:
                embed.add_field(
                    name="Top Delegates For",
                    value="\t|\t".join(
                        "[{}](https://www.nationstates.net/nation={}) ({})".format(
                            self._find_text_and_assert(e, "NATION").replace("_", " ").title(),
                            self._find_text_and_assert(e, "NATION"),
                            self._find_text_and_assert(e, "VOTES"),
                        )
                        for e in for_del_votes
                    ),
                    inline=False,
                )
            if against_del_votes:
                embed.add_field(
                    name="Top Delegates Against",
                    value="\t|\t".join(
                        "[{}](https://www.nationstates.net/nation={}) ({})".format(
                            self._find_text_and_assert(e, "NATION").replace("_", " ").title(),
                            self._find_text_and_assert(e, "NATION"),
                            self._find_text_and_assert(e, "VOTES"),
                        )
                        for e in against_del_votes
                    ),
                    inline=False,
                )
        if option & WA.VOTE:
            percent = (
                100
                * self._find_text_and_assert(root, "TOTAL_VOTES_FOR", int)
                / (
                    self._find_text_and_assert(root, "TOTAL_VOTES_FOR", int)
                    + self._find_text_and_assert(root, "TOTAL_VOTES_AGAINST", int)
                )
            )
            embed.add_field(
                name="Total Votes",
                value="For {}\t{:◄<13}\t{} Against".format(
                    self._find_text_and_assert(root, "TOTAL_VOTES_FOR"),
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    self._find_text_and_assert(root, "TOTAL_VOTES_AGAINST"),
                ),
                inline=False,
            )
        if option & WA.NATION:
            percent = (
                100
                * self._find_text_and_assert(root, "TOTAL_NATIONS_FOR", int)
                / (
                    self._find_text_and_assert(root, "TOTAL_NATIONS_FOR", int)
                    + self._find_text_and_assert(root, "TOTAL_NATIONS_AGAINST", int)
                )
            )
            embed.add_field(
                name="Total Nations",
                value="For {}\t{:◄<13}\t{} Against".format(
                    self._find_text_and_assert(root, "TOTAL_NATIONS_FOR"),
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    self._find_text_and_assert(root, "TOTAL_NATIONS_AGAINST"),
                ),
                inline=False,
            )
        # I can only blame my own buggy code for the following
        repealed_by = root.find("REPEALED_BY")
        if repealed_by is not None:
            embed.add_field(
                name="Repealed By",
                value='[Repeal "{}"](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})'.format(
                    self._find_text_and_assert(root, "NAME"),
                    self._find_text_and_assert(root, "REPEALED_BY"),
                    "2" if is_sc else "1",
                ),
                inline=False,
            )
        repeals = root.find("REPEALS_COUNCILID")
        if repeals is not None:
            embed.add_field(
                name="Repeals",
                value="[{}](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})".format(
                    self._find_text_and_assert(root, "NAME")[8:-1], repeals, "2" if is_sc else "1"
                ),
                inline=False,
            )
        embed.set_footer(text="Passed" if resolution_id else "Voting Started")
        await embed.send_to(ctx)

    # __________ SHARD __________

    @commands.command()
    async def shard(self, ctx: commands.Context, *shards: str):
        """
        Retrieves the specified info from NationStates

        Uses UNIX-style arguments. Arguments will be shards, while flags will be keywords.
        An asterisk may be used to consume the rest of the arguments at once.

        Examples:
            [p]shard --nation Darcania census --scale "65 66" --mode score
            [p]shard numnations lastupdate delegate --region * 10000 Islands
        """
        if not shards:
            return await ctx.send_help()
        request: dict = {}
        key = "q"
        ishards = iter(shards)
        for shard in ishards:
            if shard.startswith("--"):
                if key != "q":
                    return await ctx.send("No value provided for key {!r}".format(key))
                key = shard[2:]
            elif shard.startswith("*"):
                # consume the rest
                request.setdefault(key, []).append(" ".join(chain([shard[1:]], ishards)).strip())
                key = "q"
            else:
                request.setdefault(key, []).append(shard)
                key = "q"
        if key != "q":
            return await ctx.send("No value provided for key {!r}".format(key))
        root = await self._get_as_xml(**{k: " ".join(v) for k, v in request.items()})
        sans.indent(root)
        await ctx.send_interactive(
            pagify(etree.tostring(root, encoding="unicode"), shorten_by=11), "xml"
        )

    # __________ ENDORSE __________

    @commands.hybrid_command()
    async def ne(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Endorsing (NE) the specified WA nation"""
        root = await self._get_as_xml("endorsements flag fullname wa", nation=wa_nation)
        if self._find_text_and_assert(root, "UNSTATUS").lower() == "non-member":
            return await ctx.send(
                f"{self._find_text_and_assert(root, 'FULLNAME')} is not a WA member."
            )
        if not self._find_text_and_assert(root, "ENDORSEMENTS"):
            return await ctx.send(
                f"{self._find_text_and_assert(root, 'FULLNAME')} has no endorsements."
            )
        final = self._find_text_and_assert(root, "ENDORSEMENTS").split(",")
        endos = "\n".join(
            f"[{' '.join(endo.split('_')).title()}](https://www.nationstates.net/{endo})"
            for endo in final
        )
        pages = pagify(endos, page_length=1024, shorten_by=0)
        embeds: List[discord.Embed] = []
        for batch in batched(pages, 3):
            embed = discord.Embed(
                description=f"Nations endorsing [{self._find_text_and_assert(root, 'FULLNAME')}](https://www.nationstates.net/{root.get('id')})",
                color=await ctx.embed_color(),
            )
            embed.set_thumbnail(url=self._find_text_and_assert(root, "FLAG"))
            for endo in batch:
                embed.add_field(name="\u200b", value=endo, inline=True)
            embeds.append(embed)
        await menu(ctx, embeds, controls(final, paged=len(embeds) > 1), timeout=180)

    @commands.hybrid_command()
    async def nec(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Endorsing [Count] (NEC) the specified WA nation"""
        root = await self._get_as_xml(
            "census fullname wa", nation=wa_nation, scale="66", mode="score"
        )
        if self._find_text_and_assert(root, "UNSTATUS").lower() == "non-member":
            return await ctx.send(
                f"{self._find_text_and_assert(root, 'FULLNAME')} is not a WA member."
            )
        await ctx.send(
            "{:.0f} nations are endorsing {}".format(
                self._find_text_and_assert(root, "CENSUS/SCALE[@id='66']/SCORE", float),
                self._find_text_and_assert(root, "FULLNAME"),
            )
        )

    @commands.hybrid_command()
    async def spdr(self, ctx: commands.Context, *, nation: str):
        """Soft Power Disbursement Rating (SPDR, aka numerical Influence) of the specified nation"""
        root = await self._get_as_xml("census fullname", nation=nation, scale="65", mode="score")
        await ctx.send(
            "{} has {:.0f} influence".format(
                self._find_text_and_assert(root, "FULLNAME"),
                self._find_text_and_assert(root, ".//SCALE[@id='65']/SCORE", float),
            )
        )

    @commands.hybrid_command()
    async def nne(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Not Endorsing (NNE) the specified WA nation"""
        nation_root = await self._get_as_xml(
            "endorsements flag fullname region wa", nation=wa_nation
        )
        if self._find_text_and_assert(nation_root, "UNSTATUS").lower() == "non-member":
            return await ctx.send(
                f"{self._find_text_and_assert(nation_root, 'FULLNAME')} is not a WA member."
            )
        region_root = await self._get_as_xml(
            "wanations", region=self._find_text_and_assert(nation_root, "REGION")
        )
        final = set(
            (self._find_text_and_assert(region_root, "UNNATIONS") or "").split(",")
        ).difference((self._find_text_and_assert(nation_root, "ENDORSEMENTS") or "").split(","))
        if not final:
            return await ctx.send(
                f"No nation is not endorsing {self._find_text_and_assert(nation_root, 'FULLNAME')}."
            )
        endos = "\n".join(
            f"[{' '.join(endo.split('_')).title()}](https://www.nationstates.net/{endo})"
            for endo in final
        )
        pages = pagify(endos, page_length=1024, shorten_by=0)
        embeds: List[discord.Embed] = []
        for batch in batched(pages, 3):
            embed = discord.Embed(
                description=f"Nations not endorsing [{self._find_text_and_assert(nation_root, 'FULLNAME')}](https://www.nationstates.net/{nation_root.get('id')})",
                color=await ctx.embed_color(),
            )
            embed.set_thumbnail(url=self._find_text_and_assert(nation_root, "FLAG"))
            for endo in batch:
                embed.add_field(name="\u200b", value=endo, inline=True)
            embeds.append(embed)
        await menu(
            ctx,
            embeds,
            controls(final, paged=len(embeds) > 1),
            timeout=180,
        )

    @commands.hybrid_command()
    async def nnec(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Not Endorsing [Count] (NNEC) the specified WA nation"""
        nation_root = await self._get_as_xml(
            "census fullname region wa", nation=wa_nation, scale="66", mode="score"
        )
        if self._find_text_and_assert(nation_root, "UNSTATUS").lower() == "non-member":
            return await ctx.send(
                f"{self._find_text_and_assert(nation_root, 'NAME')} is not a WA member."
            )
        region_root = await self._get_as_xml(
            "numwanations", region=self._find_text_and_assert(nation_root, "REGION")
        )
        await ctx.send(
            "{:.0f} nations are not endorsing {}".format(
                self._find_text_and_assert(region_root, "NUMUNNATIONS", int)
                - self._find_text_and_assert(nation_root, ".//SCALE[@id='66']/SCORE", float),
                self._find_text_and_assert(nation_root, "FULLNAME"),
            )
        )
