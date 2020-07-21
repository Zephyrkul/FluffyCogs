import asyncio
import bisect
import re
import time
from datetime import datetime
from enum import Flag, auto
from functools import partial, reduce
from html import unescape
from io import BytesIO
from operator import or_
from typing import Generic, Optional, Type, TypeVar, Union

import discord
from redbot.core import Config, checks, commands
from redbot.core import version_info as red_version
from redbot.core.utils.chat_formatting import box, escape, pagify
from sans.api import Api

# pylint: disable=E0611
from sans.errors import HTTPException, NotFound
from sans.utils import pretty_string

from .proxyembed import ProxyEmbed


class Options(Flag):
    @classmethod
    def convert(cls, argument: str) -> "Options":
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
ZDAY_EPOCHS = (1572465600, 1572584400 + 604800)
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

    # __________ INIT __________

    def __init__(self, bot):
        super().__init__()
        Api.loop = bot.loop
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(agent=None)
        self.db_cache = None
        self.config.init_custom("NATION", 1)
        self.config.register_custom("NATION", dbid=None)

    async def initialize(self):
        agent = await self.config.agent()
        if not agent:
            if not self.bot.owner_id:
                # always False but forces owner_id to be filled
                await self.bot.is_owner(discord.Object(id=None))
            owner_id = self.bot.owner_id
            # only make the user_info request if necessary
            agent = str(self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id))
        Api.agent = f"{agent} Red-DiscordBot/{red_version}"
        self.db_cache = await self.config.custom("NATION").all()

    def cog_check(self, ctx):
        if not ctx.channel.permissions_for(ctx.me).send_messages:
            raise commands.BotMissingPermissions(["send_messages"])
        # this will also cause `[p]agent` to be blocked but this is intended
        if ctx.cog is not self:
            return True
        xra = Api.xra
        if xra:
            raise commands.CommandOnCooldown(None, time.time() - xra)
        return True

    def cog_command_error(self, ctx, error):
        # not a coro but returns one anyway
        original = getattr(error, "original", None)
        if original:
            if isinstance(original, asyncio.TimeoutError):
                return ctx.send("Request timed out.")
            if isinstance(original, HTTPException):
                return ctx.send(f"{original.status}: {original.message}")
        return ctx.bot.on_command_error(ctx, error, unhandled_by_cog=True)

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
    def _is_zday(snowflake: discord.abc.Snowflake):
        epoch = snowflake.created_at.timestamp()
        return epoch >= ZDAY_EPOCHS[0] and epoch < ZDAY_EPOCHS[1]

    # __________ LISTENERS __________

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
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
            await ctx.invoke(self.wa, int(res_id))

    # __________ STANDARD __________

    @commands.command()
    @commands.cooldown(2, 3600)
    @checks.is_owner()
    async def agent(self, ctx, *, agent: str):
        """
        Sets the user agent.

        Recommendations: https://www.nationstates.net/pages/api.html#terms
        Defaults to your username#hash
        """
        Api.agent = f"{agent} Red-DiscordBot/{red_version}"
        await self.config.agent.set(agent)
        await ctx.send(f"Agent set: {Api.agent}")

    @commands.command()
    async def nation(self, ctx, *, nation: Link[Nation]):
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
            colour=0x8BBC21 if self._is_zday(ctx.message) else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        embed.set_thumbnail(url=root.FLAG.text)
        embed.add_field(
            name=root.CATEGORY.pyval,
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
        if self._is_zday(ctx.message):
            embed.add_field(
                name="{}{}".format(
                    (root.find("ZOMBIE/ZACTION") or "No Action").title(),
                    " (Unintended)" if root.find("ZOMBIE/ZACTIONINTENDED") else "",
                ),
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root.find("ZOMBIE/SURVIVORS")),
                    self._illion(root.find("ZOMBIE/ZOMBIES")),
                    self._illion(root.find("ZOMBIE/DEAD")),
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
    async def region(self, ctx, *, region: Link[Region]):
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
        if root.DELEGATE.pyval == 0:
            delvalue = "No Delegate"
        else:
            endo = root.DELEGATEVOTES.pyval - 1
            if endo == 1:
                endo = "{:.0f} endorsement".format(endo)
            else:
                endo = "{:.0f} endorsements".format(endo)
            delvalue = "[{}](https://www.nationstates.net/nation={}) | {}".format(
                root.DELEGATE.pyval.replace("_", " ").title(), root.DELEGATE.pyval, endo
            )
        if "X" in root.DELEGATEAUTH.pyval:
            delheader = "Delegate"
        else:
            delheader = "Delegate (Non-Executive)"
        tags = {t.text for t in root.iterfind("TAGS/TAG")}
        founderless = "Founderless" in tags
        if root.FOUNDED.pyval == 0:
            founded = "in Antiquity"
        else:
            founded = root.FOUNDED.pyval
        if root.FOUNDER.pyval == 0:
            foundervalue = "No Founder"
        else:
            if founderless:
                url = "https://www.nationstates.net/page=boneyard?nation="
            else:
                url = "https://www.nationstates.net/nation="
            foundervalue = "[{}]({}{}){}".format(
                root.FOUNDER.pyval.replace("_", " ").title(),
                url,
                root.FOUNDER.pyval,
                " (Ceased to Exist)" if founderless else "",
            )
        if founderless:
            founderheader = "Founderless"
        else:
            founderheader = "Founder"
        if not root.FOUNDERAUTH.pyval or "X" not in root.FOUNDERAUTH.pyval:
            founderheader += " (Non-Executive)"
        fash = "Fascist" in tags and "Anti-Fascist" not in tags  # why do people hoard tags...
        name = "{}{}".format("\N{LOCK} " if "Password" in tags else "", root.NAME.pyval)
        if fash:
            warning = "\n**```css\n\N{HEAVY EXCLAMATION MARK SYMBOL} Region Tagged as Fascist \N{HEAVY EXCLAMATION MARK SYMBOL}\n```**"
        else:
            warning = ""
        description = "[{} nations](https://www.nationstates.net/region={}/page=list_nations) | Founded {} | Power: {}{}".format(
            root.NUMNATIONS.pyval, root.get("id"), founded, root.POWER.pyval, warning
        )
        embed = ProxyEmbed(
            title=name,
            url="https://www.nationstates.net/region={}".format(root.get("id")),
            description=description,
            timestamp=datetime.utcfromtimestamp(root.LASTUPDATE.pyval),
            colour=0x000001
            if fash
            else 0x8BBC21
            if self._is_zday(ctx.message)
            else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        if root.FLAG.pyval:
            embed.set_thumbnail(url=root.FLAG.pyval)
        embed.add_field(name=founderheader, value=foundervalue, inline=False)
        embed.add_field(name=delheader, value=delvalue, inline=False)
        if self._is_zday(ctx.message):
            embed.add_field(
                name="Zombies",
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root.find("ZOMBIE/SURVIVORS")),
                    self._illion(root.find("ZOMBIE/ZOMBIES")),
                    self._illion(root.find("ZOMBIE/DEAD")),
                ),
                inline=False,
            )
        embed.set_footer(text="Last Updated")
        await embed.send_to(ctx)

    # __________ CARDS __________

    @commands.command(usage="[season] <nation>")
    async def card(
        self, ctx, season: Optional[int] = 2, *, nation: Optional[Union[int, Link[Nation]]] = None
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
            season, nation = 2, season
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
            title=f"The {root.TYPE.pyval} of {root.NAME.pyval}",
            url=f"https://www.nationstates.net/page=deck/card={nation}/season={season}",
            colour=CARD_COLORS.get(root.CATEGORY.text, 0),
        )
        embed.set_author(name=root.CATEGORY.text.title())
        embed.set_thumbnail(
            url=f"https://www.nationstates.net/images/cards/s{season}/{root.FLAG.pyval}"
        )
        embed.add_field(
            name="Market Value (estimated)",
            value=box(root.MARKET_VALUE.text, lang="swift"),
            inline=False,
        )
        sellers, buyers = [], []
        for market in root.MARKETS.iterchildren():
            # negative price to reverse sorting
            if market.TYPE.text == "bid":
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
    async def deck(self, ctx, *, nation: Union[int, Link[Nation]]):
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
    async def wa(self, ctx, resolution_id: Optional[int] = None, *options: WA.convert):
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
        if not root.RESOLUTION:
            out = (
                unescape(root.LASTRESOLUTION.pyval)
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
        }.get(root.CATEGORY.pyval, "images/ga.jpg")
        if option & WA.TEXT:
            description = "**Category: {}**\n\n{}".format(
                root.CATEGORY.pyval, escape(root.DESC.pyval, formatting=True)
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
            description = "Category: {}".format(root.CATEGORY.pyval)
        if resolution_id:
            impl = root.IMPLEMENTED.pyval
        else:
            # mobile embeds can't handle the FUTURE
            impl = root.PROMOTED.pyval  # + (4 * 24 * 60 * 60)  # 4 Days
        embed = ProxyEmbed(
            title=root.NAME.pyval,
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
            authroot = await Api("fullname flag", nation=root.PROPOSED_BY.pyval)
        except NotFound:
            embed.set_author(
                name=root.PROPOSED_BY.text.replace("_", " ").title(),
                url="https://www.nationstates.net/page=boneyard?nation={}".format(
                    root.PROPOSED_BY.pyval
                ),
                icon_url="http://i.imgur.com/Pp1zO19.png",
            )
        else:
            embed.set_author(
                name=authroot.FULLNAME.pyval,
                url="https://www.nationstates.net/nation={}".format(root.PROPOSED_BY.pyval),
                icon_url=authroot.FLAG.pyval,
            )
        embed.set_thumbnail(url="https://www.nationstates.net/{}".format(img))
        if option & WA.DELEGATE:
            for_del_votes = sorted(
                root.iterfind("DELVOTES_FOR/DELEGATE"), key=lambda e: e.VOTES.pyval, reverse=True
            )[:10]
            against_del_votes = sorted(
                root.iterfind("DELVOTES_AGAINST/DELEGATE"),
                key=lambda e: e.VOTES.pyval,
                reverse=True,
            )[:10]
            if for_del_votes:
                embed.add_field(
                    name="Top Delegates For",
                    value="\t|\t".join(
                        "[{}](https://www.nationstates.net/nation={}) ({})".format(
                            e.NATION.text.replace("_", " ").title(), e.NATION.pyval, e.VOTES.pyval
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
                            e.NATION.text.replace("_", " ").title(), e.NATION.pyval, e.VOTES.pyval
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
                    root.NAME.pyval, root.REPEALED_BY.pyval, "2" if is_sc else "1"
                ),
                inline=False,
            )
        repeals = root.find("REPEALS_COUNCILID")
        if repeals is not None:
            embed.add_field(
                name="Repeals",
                value="[{}](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})".format(
                    root.NAME.pyval[8:-1], repeals, "2" if is_sc else "1"
                ),
                inline=False,
            )
        embed.set_footer(text="Passed" if resolution_id else "Voting Started")
        await embed.send_to(ctx)

    # __________ SHARD __________

    @commands.command()
    async def shard(self, ctx, *shards: str):
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
    async def ne(self, ctx, *, wa_nation: str):
        """Nations Endorsing (NE) the specified WA nation"""
        root = await Api("endorsements fullname wa", nation=wa_nation)
        if root.UNSTATUS.pyval.lower() == "non-member":
            return await ctx.send(f"{root.FULLNAME.pyval} is not a WA member.")
        if not root.ENDORSEMENTS.pyval:
            return await ctx.send(f"{root.FULLNAME.pyval} has no endorsements.")
        await ctx.send(
            "Nations endorsing " + root.FULLNAME.pyval,
            file=discord.File(BytesIO(root.ENDORSEMENTS.pyval.encode()), "ne.txt"),
        )

    @commands.command()
    async def nec(self, ctx, *, wa_nation: str):
        """Nations Endorsing [Count] (NEC) the specified WA nation"""
        root = await Api("census fullname wa", nation=wa_nation, scale="66", mode="score")
        if root.UNSTATUS.pyval.lower() == "non-member":
            return await ctx.send(f"{root.FULLNAME.pyval} is not a WA member.")
        await ctx.send(
            "{:.0f} nations are endorsing {}".format(
                root.find(".//SCALE[@id='66']/SCORE").pyval, root.FULLNAME.pyval
            )
        )

    @commands.command()
    async def spdr(self, ctx, *, nation: str):
        """Soft Power Disbursement Rating (SPDR, aka numerical Influence) of the specified nation"""
        root = await Api("census fullname", nation=nation, scale="65", mode="score")
        await ctx.send(
            "{} has {:.0f} influence".format(
                root.FULLNAME.pyval, root.find(".//SCALE[@id='65']/SCORE").pyval
            )
        )

    @commands.command()
    async def nne(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing (NNE) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root.UNSTATUS.pyval.lower() == "non-member":
            return await ctx.send(f"{nation_root.FULLNAME.pyval} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root.REGION.pyval)
        final = (
            set(region_root.NATIONS.pyval.split(":"))
            .intersection(wa_root.MEMBERS.pyval.split(","))
            .difference((nation_root.ENDORSEMENTS.pyval or "").split(","))
        )
        await ctx.send(
            "Nations not endorsing " + nation_root.FULLNAME.pyval,
            file=discord.File(BytesIO(",".join(final).encode()), "nne.txt"),
        )

    @commands.command()
    async def nnec(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing [Count] (NNEC) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root.UNSTATUS.pyval.lower() == "non-member":
            return await ctx.send(f"{nation_root.NAME.pyval} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root.REGION.pyval)
        final = (
            set(region_root.NATIONS.pyval.split(":"))
            .intersection(wa_root.MEMBERS.pyval.split(","))
            .difference((nation_root.ENDORSEMENTS.pyval or "").split(","))
        )
        await ctx.send(
            "{:.0f} nations are not endorsing {}".format(len(final), nation_root.FULLNAME.pyval)
        )
