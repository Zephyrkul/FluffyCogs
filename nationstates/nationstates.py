import aiohttp
import asyncio
import contextlib
import discord
import re
import time
from datetime import datetime
from enum import Flag, auto
from functools import reduce, partial
from html import unescape
from io import BytesIO
from operator import or_
from typing import Optional, Union

# pylint: disable=E0611
import sans
from sans.errors import HTTPException, NotFound
from sans.api import Api

from redbot.core import checks, commands, Config, version_info as red_version
from redbot.core.utils.chat_formatting import box, pagify, escape

# pylint: disable=E0401
from cog_shared.proxyembed import ProxyEmbed

listener = getattr(commands.Cog, "listener", lambda: lambda x: x)


LINK_RE = re.compile(
    r"(?i)\b(?:https?:\/\/)?(?:www\.)?nationstates\.net\/(?:(nation|region)=)?([-\w\s]+)\b"
)
WA_RE = re.compile(r"(?i)\b(UN|GA|SC)R?#(\d+)\b")
ZDAY_EPOCHS = (1572465600, 1572584400 + 604800)


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


class WA(Options):
    ALL = -1
    NONE = 0
    TEXT = auto()
    VOTE = auto()
    NATION = auto()
    DELEGATE = auto()


def link_extract(link: str, *, expected):
    match = LINK_RE.match(link)
    if not match:
        return link
    if (match.group(1) or "nation").lower() != expected.lower():
        raise commands.BadArgument()
    return match.group(2)


class NationStates(commands.Cog):

    # __________ INIT __________

    def __init__(self, bot):
        super().__init__()
        Api.loop = bot.loop
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(agent=None)

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

    def cog_check(self, ctx):
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

    @listener()
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
    async def nation(self, ctx, *, nation: partial(link_extract, expected="nation")):
        """Retrieves general info about a specified NationStates nation"""
        api: Api = Api(
            "census category dbid demonym2plural",
            "flag founded freedom fullname",
            "influence lastlogin motto name",
            "population region wa zombie",
            nation=nation,
            mode="score",
            scale="65 66",
        )
        try:
            root = await api
        except NotFound:
            nation = api["nation"]
            embed = ProxyEmbed(
                title=nation.replace("_", " ").title(),
                url="https://www.nationstates.net/page="
                "boneyard?nation={}".format("_".join(nation.split()).lower()),
                description="This nation does not exist.",
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            embed.set_thumbnail(url="http://i.imgur.com/Pp1zO19.png")
            return await embed.send_to(ctx)
        endo = root["CENSUS/SCALE[@id='66']/SCORE"]
        if endo == 1:
            endo = "{:.0f} endorsement".format(endo)
        else:
            endo = "{:.0f} endorsements".format(endo)
        if root["FOUNDED"] == 0:
            root["FOUNDED"] = "in Antiquity"
        embed = ProxyEmbed(
            title=root["FULLNAME"],
            url="https://www.nationstates.net/nation={}".format(root.get("id")),
            description="[{}](https://www.nationstates.net/region={})"
            " | {} {} | Founded {}".format(
                root["REGION"],
                "_".join(root["REGION"].lower().split()),
                self._illion(root["POPULATION"]),
                root["DEMONYM2PLURAL"],
                root["FOUNDED"],
            ),
            timestamp=datetime.utcfromtimestamp(root["LASTLOGIN"]),
            colour=0x8BBC21 if self._is_zday(ctx.message) else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        embed.set_thumbnail(url=root["FLAG"])
        embed.add_field(
            name=root["CATEGORY"],
            value="{}\t|\t{}\t|\t{}".format(
                root["FREEDOM/CIVILRIGHTS"],
                root["FREEDOM/ECONOMY"],
                root["FREEDOM/POLITICALFREEDOM"],
            ),
            inline=False,
        )
        embed.add_field(
            name=root["UNSTATUS"],
            value="{} | {:.0f} influence ({})".format(
                endo, root["CENSUS/SCALE[@id='65']/SCORE"], root["INFLUENCE"]
            ),
            inline=False,
        )
        if self._is_zday(ctx.message):
            embed.add_field(
                name="{}{}".format(
                    (root["ZOMBIE/ZACTION"] or "No Action").title(),
                    " (Unintended)" if root["ZOMBIE/ZACTIONINTENDED"] else "",
                ),
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root["ZOMBIE/SURVIVORS"]),
                    self._illion(root["ZOMBIE/ZOMBIES"]),
                    self._illion(root["ZOMBIE/DEAD"]),
                ),
                inline=False,
            )
        embed.add_field(
            name="Cards",
            value=(
                "[{0}'s Deck](https://www.nationstates.net/page=deck/nation={1})\t|"
                "\t[{0}'s Card](https://www.nationstates.net/page=deck/card={2})".format(
                    root["NAME"], root.get("id"), root["DBID"]
                )
            ),
        )
        embed.set_footer(text="Last Active")
        await embed.send_to(ctx)

    @commands.command()
    async def region(self, ctx, *, region: partial(link_extract, expected="region")):
        """Retrieves general info about a specified NationStates region"""
        api: Api = Api(
            "delegate delegateauth delegatevotes flag founded founder founderauth lastupdate name numnations power tags zombie",
            region=region,
        )
        try:
            root = await api
        except NotFound:
            region = api["region"]
            embed = ProxyEmbed(
                title=region.replace("_", " ").title(), description="This region does not exist."
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            return await embed.send_to(ctx)
        if root["DELEGATE"] == 0:
            delvalue = "No Delegate"
        else:
            endo = root["DELEGATEVOTES"] - 1
            if endo == 1:
                endo = "{:.0f} endorsement".format(endo)
            else:
                endo = "{:.0f} endorsements".format(endo)
            delvalue = "[{}](https://www.nationstates.net/nation={}) | {}".format(
                root["DELEGATE"].replace("_", " ").title(), root["DELEGATE"], endo
            )
        if "X" in root["DELEGATEAUTH"]:
            delheader = "Delegate"
        else:
            delheader = "Delegate (Non-Executive)"
        tags = {t.text for t in root.iterfind("TAGS/TAG")}
        founderless = "Founderless" in tags
        if root["FOUNDED"] == 0:
            founded = "in Antiquity"
        else:
            founded = root["FOUNDED"]
        if root["FOUNDER"] == 0:
            foundervalue = "No Founder"
        else:
            if founderless:
                url = "https://www.nationstates.net/page=boneyard?nation="
            else:
                url = "https://www.nationstates.net/nation="
            foundervalue = "[{}]({}{}){}".format(
                root["FOUNDER"].replace("_", " ").title(),
                url,
                root["FOUNDER"],
                " (Ceased to Exist)" if founderless else "",
            )
        if founderless:
            founderheader = "Founderless"
        else:
            founderheader = "Founder"
        if not root["FOUNDERAUTH"] or "X" not in root["FOUNDERAUTH"]:
            founderheader += " (Non-Executive)"
        fash = "Fascist" in tags and "Anti-Fascist" not in tags  # why do people hoard tags...
        name = "{}{}".format("\N{LOCK} " if "Password" in tags else "", root["NAME"])
        if fash:
            warning = "\n**```css\n\N{HEAVY EXCLAMATION MARK SYMBOL} Region Tagged as Fascist \N{HEAVY EXCLAMATION MARK SYMBOL}\n```**"
        else:
            warning = ""
        description = "[{} nations](https://www.nationstates.net/region={}/page=list_nations) | Founded {} | Power: {}{}".format(
            root["NUMNATIONS"], root.get("id"), founded, root["POWER"], warning
        )
        embed = ProxyEmbed(
            title=name,
            url="https://www.nationstates.net/region={}".format(root.get("id")),
            description=description,
            timestamp=datetime.utcfromtimestamp(root["LASTUPDATE"]),
            colour=0x000001
            if fash
            else 0x8BBC21
            if self._is_zday(ctx.message)
            else await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        if root["FLAG"]:
            embed.set_thumbnail(url=root["FLAG"])
        embed.add_field(name=founderheader, value=foundervalue, inline=False)
        embed.add_field(name=delheader, value=delvalue, inline=False)
        if self._is_zday(ctx.message):
            embed.add_field(
                name="Zombies",
                value="Survivors: {} | Zombies: {} | Dead: {}".format(
                    self._illion(root["ZOMBIE/SURVIVORS"]),
                    self._illion(root["ZOMBIE/ZOMBIES"]),
                    self._illion(root["ZOMBIE/DEAD"]),
                ),
                inline=False,
            )
        embed.set_footer(text="Last Updated")
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
        if root["RESOLUTION"] is None:
            out = (
                unescape(root["LASTRESOLUTION"])
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
        root = root["RESOLUTION"]
        img = {
            "Commendation": "images/commend.png",
            "Condemnation": "images/condemn.png",
            "Liberation": "images/liberate.png",
        }.get(root["CATEGORY"], "images/ga.jpg")
        if option & WA.TEXT:
            description = "**Category: {}**\n\n{}".format(
                root["CATEGORY"], escape(root["DESC"], formatting=True)
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
            description = "Category: {}".format(root["CATEGORY"])
        if resolution_id:
            impl = root["IMPLEMENTED"]
        else:
            # mobile embeds can't handle the FUTURE
            impl = root["PROMOTED"]  # + (4 * 24 * 60 * 60)  # 4 Days
        embed = ProxyEmbed(
            title=root["NAME"],
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
            authroot = await Api("fullname flag", nation=root["PROPOSED_BY"])
        except NotFound:
            embed.set_author(
                name=" ".join(root["PROPOSED_BY"].split("_")).title(),
                url="https://www.nationstates.net/page=boneyard?nation={}".format(
                    root["PROPOSED_BY"]
                ),
                icon_url="http://i.imgur.com/Pp1zO19.png",
            )
        else:
            embed.set_author(
                name=authroot["FULLNAME"],
                url="https://www.nationstates.net/nation={}".format(root["PROPOSED_BY"]),
                icon_url=authroot["FLAG"],
            )
        embed.set_thumbnail(url="https://www.nationstates.net/{}".format(img))
        if option & WA.DELEGATE:
            for_del_votes = sorted(
                root.iterfind("DELVOTES_FOR/DELEGATE"), key=lambda e: e["VOTES"], reverse=True
            )[:10]
            against_del_votes = sorted(
                root.iterfind("DELVOTES_AGAINST/DELEGATE"), key=lambda e: e["VOTES"], reverse=True
            )[:10]
            if for_del_votes:
                embed.add_field(
                    name="Top Delegates For",
                    value="\t|\t".join(
                        "[{}](https://www.nationstates.net/nation={}) ({})".format(
                            e["NATION"].replace("_", " ").title(), e["NATION"], e["VOTES"]
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
                            e["NATION"].replace("_", " ").title(), e["NATION"], e["VOTES"]
                        )
                        for e in against_del_votes
                    ),
                    inline=False,
                )
        if option & WA.VOTE:
            percent = (
                100
                * root["TOTAL_VOTES_FOR"]
                / (root["TOTAL_VOTES_FOR"] + root["TOTAL_VOTES_AGAINST"])
            )
            embed.add_field(
                name="Total Votes",
                value="For {}\t{:◄<13}\t{} Against".format(
                    root["TOTAL_VOTES_FOR"],
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    root["TOTAL_VOTES_AGAINST"],
                ),
                inline=False,
            )
        if option & WA.NATION:
            percent = (
                100
                * root["TOTAL_NATIONS_FOR"]
                / (root["TOTAL_NATIONS_FOR"] + root["TOTAL_NATIONS_AGAINST"])
            )
            embed.add_field(
                name="Total Nations",
                value="For {}\t{:◄<13}\t{} Against".format(
                    root["TOTAL_NATIONS_FOR"],
                    "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                    root["TOTAL_NATIONS_AGAINST"],
                ),
                inline=False,
            )
        # I can only blame my own buggy code for the following
        try:
            root["REPEALED_BY"]
        except KeyError:
            pass
        else:
            embed.add_field(
                name="Repealed By",
                value='[Repeal "{}"](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})'.format(
                    root["NAME"], root["REPEALED_BY"], "2" if is_sc else "1"
                ),
                inline=False,
            )
        try:
            root["REPEALS_COUNCILID"]
        except KeyError:
            pass
        else:
            embed.add_field(
                name="Repeals",
                value="[{}](https://www.nationstates.net/page=WA_past_resolution/id={}/council={})".format(
                    root["NAME"][8:-1], root["REPEALS_COUNCILID"], "2" if is_sc else "1"
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
        root = await Api(request)
        await ctx.send_interactive(pagify(root.to_pretty_string(), shorten_by=11), "xml")

    # __________ ENDORSE __________

    @commands.command()
    async def ne(self, ctx, *, wa_nation: str):
        """Nations Endorsing (NE) the specified WA nation"""
        root = await Api("endorsements fullname wa", nation=wa_nation)
        if root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{root['FULLNAME']} is not a WA member.")
        if not root["ENDORSEMENTS"]:
            return await ctx.send(f"{root['FULLNAME']} has no endorsements.")
        await ctx.send(
            "Nations endorsing " + root["FULLNAME"],
            file=discord.File(BytesIO(root["ENDORSEMENTS"].encode()), "ne.txt"),
        )

    @commands.command()
    async def nec(self, ctx, *, wa_nation: str):
        """Nations Endorsing [Count] (NEC) the specified WA nation"""
        root = await Api("census fullname wa", nation=wa_nation, scale="66", mode="score")
        if root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{root['FULLNAME']} is not a WA member.")
        await ctx.send(
            "{:.0f} nations are endorsing {}".format(
                root[".//SCALE[@id='66']/SCORE"], root["FULLNAME"]
            )
        )

    @commands.command()
    async def spdr(self, ctx, *, nation: str):
        """Soft Power Disbursement Rating (SPDR, aka numerical Influence) of the specified nation"""
        root = await Api("census fullname", nation=nation, scale="65", mode="score")
        await ctx.send(
            "{} has {:.0f} influence".format(root["FULLNAME"], root[".//SCALE[@id='65']/SCORE"])
        )

    @commands.command()
    async def nne(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing (NNE) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{nation_root['FULLNAME']} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root["REGION"])
        final = (
            set(region_root["NATIONS"].split(":"))
            .intersection(wa_root["MEMBERS"].split(","))
            .difference((nation_root["ENDORSEMENTS"] or "").split(","))
        )
        await ctx.send(
            "Nations not endorsing " + nation_root["FULLNAME"],
            file=discord.File(BytesIO(",".join(final).encode()), "nne.txt"),
        )

    @commands.command()
    async def nnec(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing [Count] (NNEC) the specified WA nation"""
        nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        if nation_root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{nation_root['NAME']} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root["REGION"])
        final = (
            set(region_root["NATIONS"].split(":"))
            .intersection(wa_root["MEMBERS"].split(","))
            .difference((nation_root["ENDORSEMENTS"] or "").split(","))
        )
        await ctx.send(
            "{:.0f} nations are not endorsing {}".format(len(final), nation_root["FULLNAME"])
        )
