import asyncio
import bisect
import heapq
import operator
import re
import time
from datetime import datetime
from enum import Flag, auto
from functools import partial, reduce
from html import unescape
from io import BytesIO
from operator import or_
from typing import Dict, Generic, List, Literal, Optional, Tuple, Type, TypeVar, Union

import discord
from proxyembed import ProxyEmbed
from redbot.core import Config, checks, commands, version_info as red_version
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, escape, humanize_list, pagify
from sans.api import Api

# pylint: disable=E0611
from sans.errors import HTTPException, NotFound
from sans.utils import pretty_string


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
ZDAY_START = 1635627600
T = TypeVar("T", bound=Options)


class Link(str, Generic[T]):
    @classmethod
    def __class_getitem__(cls, item: Type[T]):
        return partial(cls.link_extract, expected=item.__name__)

    @staticmethod
    def link_extract(link: str, *, expected: str):
        match = LINK_RE.match(link)
        if not match:
            return "_".join(link.strip('"<>').casefold().split())
        if (match.group(1) or "nation").casefold() == expected.casefold():
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
        Api.loop = bot.loop
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(agent=None)
        self.db_cache: Dict[str, Dict[Literal["dbid"], int]] = {}
        self.config.init_custom("NATION", 1)
        self.config.register_custom("NATION", dbid=None)
        self.cog_ready = asyncio.Event()
        asyncio.create_task(self.initialize())

    async def initialize(self):
        agent = await self.config.agent()
        if not agent:
            await self.bot.wait_until_red_ready()
            if not self.bot.owner_ids:
                # always False but forces owner_ids to be filled
                await self.bot.is_owner(discord.Object(id=None))
            owner_ids = self.bot.owner_ids
            # only make the user_info request if necessary
            agent = humanize_list(
                [str(self.bot.get_user(id) or await self.bot.fetch_user(id)) for id in owner_ids]
            )
        Api.agent = f"{agent} Red-DiscordBot/{red_version}"
        self.db_cache = await self.config.custom("NATION").all()
        self.cog_ready.set()

    async def cog_before_invoke(self, ctx):
        # this will also cause `[p]agent` to be blocked but this is intended
        if ctx.cog is not self:
            return
        await self.cog_ready.wait()
        xra = Api.xra
        if xra:
            raise commands.CommandOnCooldown(None, time.time() - xra)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        original = getattr(error, "original", None)
        if original:
            if isinstance(original, asyncio.TimeoutError):
                return ctx.send("Request timed out.")
            if isinstance(original, HTTPException):
                return ctx.send(f"{original.status}: {original.message}")
        return await ctx.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    # __________ UTILS __________

    @staticmethod
    def _illion(num):
        illion = ("million", "billion", "trillion", "quadrillion")
        num = float(num)
        index = 0
        while num >= 1000:
            index += 1
            num /= 1000
        return "{} {}".format(round(num, 3), illion[index])

    @staticmethod
    def _is_zday(snowflake: discord.abc.Snowflake, *, dev: bool = False):
        epoch = snowflake.created_at.timestamp()
        start, end = ZDAY_START - 259200 * dev, ZDAY_START + 723600
        return start <= epoch < end

    # __________ LISTENERS __________

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.author.bot:
            return
        if not await self.bot.message_eligible_as_command(
            message
        ) or await self.bot.cog_disabled_in_guild(self, message.guild):
            return
        await self.cog_ready.wait()
        ctx = await self.bot.get_context(message)
        if ctx.valid:
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
            if await self.wa.can_run(ctx):
                await ctx.invoke(self.wa, int(res_id), WA.NONE)

    # __________ STANDARD __________

    @commands.command(cooldown_after_parsing=True)
    @commands.cooldown(2, 3600)
    @checks.is_owner()
    async def agent(self, ctx: commands.Context, *, agent: str):
        """
        Sets the user agent.

        Recommendations: https://www.nationstates.net/pages/api.html#terms
        Defaults to your username#hash
        """
        Api.agent = f"{agent} Red-DiscordBot/{red_version}"
        await self.config.agent.set(agent)
        await ctx.send(f"Agent set: {Api.agent}")

    @commands.command()
    async def nation(self, ctx: commands.Context, *, nation: Link[Nation]):
        """Retrieves general info about a specified NationStates nation"""
        api: Api = Api(
            "census category dbid",
            "demonym2plural flag founded freedom",
            "fullname influence lastlogin motto",
            "name population region wa zombie",
            nation=nation,
            mode="score",
            scale="65 66",
        )
        try:
            root = await api
        except NotFound:
            embed = ProxyEmbed(
                title=nation.replace("_", " ").title(),
                url="https://www.nationstates.net/page=" "boneyard?nation={}".format(nation),
                description="This nation does not exist.",
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            embed.set_thumbnail(url="http://i.imgur.com/Pp1zO19.png")
            return await embed.send_to(ctx)
        n_id = root.get("id")
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": root.DBID.pyval}
            await self.config.custom("NATION", n_id).dbid.set(root.DBID.pyval)
        endo = root.find("CENSUS/SCALE[@id='66']/SCORE").pyval
        if endo == 1:
            endo = "{:.0f} endorsement".format(endo)
        else:
            endo = "{:.0f} endorsements".format(endo)
        founded = root.FOUNDED.pyval or "in Antiquity"
        is_zday = self._is_zday(ctx.message, dev=ctx.author.id == 215640856839979008)
        embed = ProxyEmbed(
            title=root.FULLNAME.text,
            url="https://www.nationstates.net/nation={}".format(root.get("id")),
            description="[{}](https://www.nationstates.net/region={})"
            " | {} {} | Founded {}".format(
                root.REGION.text,
                "_".join(root.REGION.text.lower().split()),
                self._illion(root.POPULATION.pyval),
                root["DEMONYM2PLURAL"].text,
                founded,
            ),
            timestamp=datetime.utcfromtimestamp(root.LASTLOGIN.pyval),
            colour=0x8BBC21 if is_zday else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        embed.set_thumbnail(url=root.FLAG.text)
        embed.add_field(
            name=root.CATEGORY.text,
            value="{}\t|\t{}\t|\t{}".format(
                root.find("FREEDOM/CIVILRIGHTS"),
                root.find("FREEDOM/ECONOMY"),
                root.find("FREEDOM/POLITICALFREEDOM"),
            ),
            inline=False,
        )
        embed.add_field(
            name=root.UNSTATUS.text,
            value="{} | {:.0f} influence ({})".format(
                endo, root.find("CENSUS/SCALE[@id='65']/SCORE").pyval, root.INFLUENCE.text
            ),
            inline=False,
        )
        if is_zday:
            embed.add_field(
                name="{}{}".format(
                    (root.ZOMBIE.ZACTION.text or "No Action").title(),
                    " (Unintended)" if root.ZOMBIE.ZACTIONINTENDED.text else "",
                ),
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root.ZOMBIE.SURVIVORS),
                    self._illion(root.ZOMBIE.ZOMBIES),
                    self._illion(root.ZOMBIE.DEAD),
                ),
                inline=False,
            )
        embed.add_field(
            name="Cards",
            value=(
                "[{0}'s Deck](https://www.nationstates.net/page=deck/nation={1})\t|"
                "\t[{0}'s Card](https://www.nationstates.net/page=deck/card={2})".format(
                    root.NAME.text, n_id, root.DBID.text
                )
            ),
        )
        embed.set_footer(text="Last Active")
        await embed.send_to(ctx)

    @commands.command()
    async def region(self, ctx: commands.Context, *, region: Link[Region]):
        """Retrieves general info about a specified NationStates region"""
        api: Api = Api(
            "delegate delegateauth delegatevotes flag founded founder founderauth lastupdate name numnations power tags zombie",
            region=region,
        )
        try:
            root = await api
        except NotFound:
            embed = ProxyEmbed(
                title=region.replace("_", " ").title(), description="This region does not exist."
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            return await embed.send_to(ctx)
        if root.DELEGATE.text == "0":
            delvalue = "No Delegate"
        else:
            endo = root.DELEGATEVOTES.pyval - 1
            if endo == 1:
                endo = "{:.0f} endorsement".format(endo)
            else:
                endo = "{:.0f} endorsements".format(endo)
            delvalue = "[{}](https://www.nationstates.net/nation={}) | {}".format(
                root.DELEGATE.text.replace("_", " ").title(), root.DELEGATE.text, endo
            )
        if "X" in root.DELEGATEAUTH.text:
            delheader = "Delegate"
        else:
            delheader = "Delegate (Non-Executive)"
        tags = {t.text for t in root.iterfind("TAGS/TAG")}
        founderless = "Founderless" in tags
        founded = "in Antiquity" if root.FOUNDED.pyval == 0 else root.FOUNDED.pyval
        if root.FOUNDER.text == "0":
            foundervalue = "No Founder"
        else:
            if founderless:
                url = "https://www.nationstates.net/page=boneyard?nation="
            else:
                url = "https://www.nationstates.net/nation="
            foundervalue = "[{}]({}{}){}".format(
                root.FOUNDER.text.replace("_", " ").title(),
                url,
                root.FOUNDER.text,
                " (Ceased to Exist)" if founderless else "",
            )
        founderheader = "Founderless" if founderless else "Founder"
        if not root.FOUNDERAUTH.text or "X" not in root.FOUNDERAUTH.text:
            founderheader += " (Non-Executive)"
        fash = "Fascist" in tags and "Anti-Fascist" not in tags  # why do people hoard tags...
        name = "{}{}".format("\N{LOCK} " if "Password" in tags else "", root.NAME.text)
        warning = (
            "\n**```css\n\N{HEAVY EXCLAMATION MARK SYMBOL} Region Tagged as Fascist \N{HEAVY EXCLAMATION MARK SYMBOL}\n```**"
            if fash
            else ""
        )

        description = "[{} nations](https://www.nationstates.net/region={}/page=list_nations) | Founded {} | Power: {}{}".format(
            root.NUMNATIONS.pyval, root.get("id"), founded, root.POWER.text, warning
        )
        is_zday = self._is_zday(ctx.message, dev=ctx.author.id == 215640856839979008)
        embed = ProxyEmbed(
            title=name,
            url="https://www.nationstates.net/region={}".format(root.get("id")),
            description=description,
            timestamp=datetime.utcfromtimestamp(root.LASTUPDATE.pyval),
            colour=0x000001 if fash else 0x8BBC21 if is_zday else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        if root.FLAG.text:
            embed.set_thumbnail(url=root.FLAG.text)
        embed.add_field(name=founderheader, value=foundervalue, inline=False)
        embed.add_field(name=delheader, value=delvalue, inline=False)
        if is_zday:
            embed.add_field(
                name="Zombies",
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root.ZOMBIE.SURVIVORS),
                    self._illion(root.ZOMBIE.ZOMBIES),
                    self._illion(root.ZOMBIE.DEAD),
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
            api = Api("dbid", nation=nation)
            try:
                root = await api
            except NotFound:
                return await ctx.send(
                    f"Nation {nation!r} does not exist. "
                    "Please provide its card ID instead, and I'll remember it for next time."
                )
            n_id, nation = root.get("id"), root.DBID.pyval
            self.db_cache[n_id] = {"dbid": nation}
            await self.config.custom("NATION", n_id).dbid.set(nation)
        else:
            n_id, nation = None, self.db_cache.get(nation, {}).get("dbid", nation)
        assert isinstance(nation, int), repr(nation)
        api = Api("card info markets", cardid=nation, season=season)
        root = await api
        if not root.countchildren():
            if n_id:
                return await ctx.send(f"No such S{season} card for nation {n_id!r}.")
            return await ctx.send(f"No such S{season} card for ID {nation!r}.")
        n_id = root.NAME.text.casefold().replace(" ", "_")
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": nation}
            await self.config.custom("NATION", n_id).dbid.set(nation)
        embed = ProxyEmbed(
            title=f"The {root.TYPE.text} of {root.NAME.text}",
            url=f"https://www.nationstates.net/page=deck/card={nation}/season={season}",
            colour=CARD_COLORS.get(root.CATEGORY.text, 0),
        )
        embed.set_author(name=root.CATEGORY.text.title())
        embed.set_thumbnail(
            url=f"https://www.nationstates.net/images/cards/s{season}/{root.FLAG.text}"
        )
        embed.add_field(
            name="Market Value (estimated)",
            value=box(root.MARKET_VALUE.text, lang="swift"),
            inline=False,
        )
        sellers: List[Tuple[int, str]] = []
        buyers: List[Tuple[int, str]] = []
        for market in root.MARKETS.iterchildren():
            if market.TYPE.text == "bid":
                # negative price to reverse sorting
                bisect.insort(
                    buyers, (-market.PRICE.pyval, market.NATION.text.replace("_", " ").title())
                )
            elif market.TYPE.text == "ask":
                bisect.insort(
                    sellers, (market.PRICE.pyval, market.NATION.text.replace("_", " ").title())
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
            api = Api("cards info", nationid=nation)
        else:
            api = Api("cards info", nationname=nation)
        root = await api
        if not root.INFO.countchildren():
            if is_id:
                return await ctx.send(f"No such deck for ID {nation}.")
            return await ctx.send(f"No such deck for nation {nation!r}.")
        n_id = root.INFO.NAME.text
        if n_id not in self.db_cache:
            self.db_cache[n_id] = {"dbid": root.INFO.ID.pyval}
            await self.config.custom("NATION", n_id).dbid.set(root.INFO.ID.pyval)
        embed = ProxyEmbed(
            title=n_id.replace("_", " ").title(),
            url=f"https://www.nationstates.net/page=deck/nation={n_id}",
            description=f"{root.INFO.NUM_CARDS.text} cards",
            colour=await ctx.embed_colour(),
            timestamp=datetime.utcfromtimestamp(root.INFO.LAST_VALUED.pyval),
        )
        embed.add_field(name="Bank", value=root.INFO.BANK.text)
        embed.add_field(
            name="Deck Value",
            value=f"[{root.INFO.DECK_VALUE.text}](https://www.nationstates.net/nation={n_id}/detail=trend/censusid=86)"
            f"\nRanked #{root.INFO.RANK.text} worldwide, #{root.INFO.REGION_RANK.text} regionally.",
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
        root = await Api(request, q=shards)
        if not root.RESOLUTION.countchildren():
            out = (
                unescape(root.LASTRESOLUTION.text)
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
        root = root.RESOLUTION
        img = {
            "Commendation": "images/commend.png",
            "Condemnation": "images/condemn.png",
            "Liberation": "images/liberate.png",
        }.get(root.CATEGORY.text, "images/ga.jpg")
        if option & WA.TEXT:
            description = "**Category: {}**\n\n{}".format(
                root.CATEGORY.text, escape(root.DESC.text, formatting=True)
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
            description = "Category: {}".format(root.CATEGORY.text)
        if resolution_id:
            impl = root.IMPLEMENTED.pyval
        else:
            # mobile embeds can't handle the FUTURE
            impl = root.PROMOTED.pyval  # + (4 * 24 * 60 * 60)  # 4 Days
        embed = ProxyEmbed(
            title=root.NAME.text,
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
            authroot = await Api("fullname flag", nation=root.PROPOSED_BY.text)
        except NotFound:
            embed.set_author(
                name=root.PROPOSED_BY.text.replace("_", " ").title(),
                url="https://www.nationstates.net/page=boneyard?nation={}".format(
                    root.PROPOSED_BY.text
                ),
                icon_url="http://i.imgur.com/Pp1zO19.png",
            )
        else:
            embed.set_author(
                name=authroot.FULLNAME.text,
                url="https://www.nationstates.net/nation={}".format(root.PROPOSED_BY.text),
                icon_url=authroot.FLAG.text,
            )
        embed.set_thumbnail(url="https://www.nationstates.net/{}".format(img))
        if option & WA.DELEGATE:
            for_del_votes = heapq.nlargest(
                10, root.iterfind("DELVOTES_FOR/DELEGATE"), key=operator.attrgetter("VOTES.pyval")
            )
            against_del_votes = heapq.nlargest(
                10,
                root.iterfind("DELVOTES_AGAINST/DELEGATE"),
                key=operator.attrgetter("VOTES.pyval"),
            )
            if for_del_votes:
                embed.add_field(
                    name="Top Delegates For",
                    value="\t|\t".join(
                        "[{}](https://www.nationstates.net/nation={}) ({})".format(
                            e.NATION.text.replace("_", " ").title(), e.NATION.text, e.VOTES.text
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
                            e.NATION.text.replace("_", " ").title(), e.NATION.text, e.VOTES.text
                        )
                        for e in against_del_votes
                    ),
                    inline=False,
                )
        if option & WA.VOTE:
            percent = (
                100
                * root.TOTAL_VOTES_FOR.pyval
                / (root.TOTAL_VOTES_FOR.pyval + root.TOTAL_VOTES_AGAINST.pyval)
            )
            embed.add_field(
                name="Total Votes",
                value="For {}\t{:◄<13}\t{} Against".format(
                    root.TOTAL_VOTES_FOR.pyval,
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    root.TOTAL_VOTES_AGAINST.pyval,
                ),
                inline=False,
            )
        if option & WA.NATION:
            percent = (
                100
                * root.TOTAL_NATIONS_FOR.pyval
                / (root.TOTAL_NATIONS_FOR.pyval + root.TOTAL_NATIONS_AGAINST.pyval)
            )
            embed.add_field(
                name="Total Nations",
                value="For {}\t{:◄<13}\t{} Against".format(
                    root.TOTAL_NATIONS_FOR.pyval,
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    root.TOTAL_NATIONS_AGAINST.pyval,
                ),
                inline=False,
            )
        # I can only blame my own buggy code for the following
        repealed_by = root.find("REPEALED_BY")
        if repealed_by is not None:
            embed.add_field(
                name="Repealed By",
                value='[Repeal "{}"](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})'.format(
                    root.NAME.text, root.REPEALED_BY.text, "2" if is_sc else "1"
                ),
                inline=False,
            )
        repeals = root.find("REPEALS_COUNCILID")
        if repeals is not None:
            embed.add_field(
                name="Repeals",
                value="[{}](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})".format(
                    root.NAME.text[8:-1], repeals, "2" if is_sc else "1"
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
        for i, shard in enumerate(shards):
            if shard.startswith("--"):
                if key != "q":
                    return await ctx.send("No value provided for key {!r}".format(key))
                key = shard[2:]
            elif shard.startswith("*"):
                request.setdefault(key, []).append(" ".join((shard[1:], *shards[i + 1 :])).strip())
                key = "q"
                break
            else:
                request.setdefault(key, []).append(shard)
                key = "q"
        if key != "q":
            return await ctx.send("No value provided for key {!r}".format(key))
        root = await Api(**request)
        await ctx.send_interactive(pagify(pretty_string(root), shorten_by=11), "xml")

    # __________ ENDORSE __________

    @commands.command()
    async def ne(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Endorsing (NE) the specified WA nation"""
        root = await Api("endorsements fullname wa", nation=wa_nation)
        if root.UNSTATUS.text.lower() == "non-member":
            return await ctx.send(f"{root.FULLNAME.text} is not a WA member.")
        if not root.ENDORSEMENTS.text:
            return await ctx.send(f"{root.FULLNAME.text} has no endorsements.")
        await ctx.send(
            "Nations endorsing " + root.FULLNAME.text,
            file=discord.File(BytesIO(root.ENDORSEMENTS.text.encode()), "ne.txt"),
        )

    @commands.command()
    async def nec(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Endorsing [Count] (NEC) the specified WA nation"""
        root = await Api("census fullname wa", nation=wa_nation, scale="66", mode="score")
        if root.UNSTATUS.text.lower() == "non-member":
            return await ctx.send(f"{root.FULLNAME.text} is not a WA member.")
        await ctx.send(
            "{:.0f} nations are endorsing {}".format(
                root.find(".//SCALE[@id='66']/SCORE").pyval, root.FULLNAME.text
            )
        )

    @commands.command()
    async def spdr(self, ctx: commands.Context, *, nation: str):
        """Soft Power Disbursement Rating (SPDR, aka numerical Influence) of the specified nation"""
        root = await Api("census fullname", nation=nation, scale="65", mode="score")
        await ctx.send(
            "{} has {:.0f} influence".format(
                root.FULLNAME.text, root.find(".//SCALE[@id='65']/SCORE").pyval
            )
        )

    @commands.command()
    async def nne(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Not Endorsing (NNE) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root.UNSTATUS.text.lower() == "non-member":
            return await ctx.send(f"{nation_root.FULLNAME.text} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root.REGION.text)
        final = (
            set(region_root.NATIONS.text.split(":"))
            .intersection(wa_root.MEMBERS.text.split(","))
            .difference((nation_root.ENDORSEMENTS.text or "").split(","))
        )
        await ctx.send(
            "Nations not endorsing " + nation_root.FULLNAME.text,
            file=discord.File(BytesIO(",".join(final).encode()), "nne.txt"),
        )

    @commands.command()
    async def nnec(self, ctx: commands.Context, *, wa_nation: str):
        """Nations Not Endorsing [Count] (NNEC) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root.UNSTATUS.text.lower() == "non-member":
            return await ctx.send(f"{nation_root.NAME.text} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root.REGION.text)
        final = (
            set(region_root.NATIONS.text.split(":"))
            .intersection(wa_root.MEMBERS.text.split(","))
            .difference((nation_root.ENDORSEMENTS.text or "").split(","))
        )
        await ctx.send(
            "{:.0f} nations are not endorsing {}".format(len(final), nation_root.FULLNAME.text)
        )
