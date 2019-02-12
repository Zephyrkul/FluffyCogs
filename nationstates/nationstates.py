import aiohttp
import contextlib
import discord
import re
from datetime import datetime
from enum import Flag, auto
from functools import reduce
from html import unescape
from io import BytesIO
from typing import Optional

# pylint: disable=E0611
import sans
from sans.errors import *
from sans.api import Api

from redbot.core import checks, commands, Config, version_info as red_version
from redbot.core.utils.chat_formatting import box, pagify

Cog = getattr(commands, "Cog", object)


LINK_RE = re.compile(
    r"(?i)\b(?:(?:https?:\/\/)?(?:www\.)?nationstates\.net\/(?:(nation|region)=)?)?([-\w\s]+)\b"
)
WA_RE = re.compile(r"(?i)\b(UN|GA|SC)R?#(\d+)\b")


class WAOptions(Flag):
    NONE = 0
    TEXT = auto()
    VOTE = auto()
    NATION = auto()
    DELEGATE = auto()

    @classmethod
    def convert(cls, argument: str):
        argument = argument.upper().rstrip("S")
        try:
            return cls[argument]
        except KeyError as ke:
            raise commands.BadArgument() from ke


def link_extract(link: str):
    match = LINK_RE.match(link)
    if not match:
        return link
    return match.group(2)


class NationStates(Cog):

    # __________ INIT __________

    def __init__(self, bot):
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
            agent = str(self.bot.get_user(owner_id) or await self.bot.get_user_info(owner_id))
        Api.agent = f"{agent} Red-DiscordBot/{red_version}"

    # __________ UTILS __________

    @staticmethod
    async def _maybe_embed(dest, embed):
        try:
            return await dest.send(embed=embed)
        except discord.Forbidden as e:
            raise commands.BotMissingPermissions([("embed_links", True)]) from e

    @staticmethod
    def _illion(num):
        illion = ("million", "billion", "trillion", "quadrillion")
        num = float(num)
        index = 0
        while num >= 1000:
            index += 1
            num /= 1000
        return "{} {}".format(round(num, 3), illion[index])

    # __________ LISTENERS __________

    async def on_message(self, message):
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
            else:
                if not ctx.channel.permissions_for(ctx.me).embed_links:
                    await ctx.send(
                        f"https://www.nationstates.net/page=WA_past_resolution/id={res_id}/council={council}"
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
    @commands.bot_has_permissions(embed_links=True)
    async def nation(self, ctx, *, nation: link_extract):
        """Retrieves general info about a specified NationStates nation"""
        nation = Api(
            "census category dbid demonym2plural",
            "flag founded freedom fullname",
            "influence lastlogin motto name",
            "population region wa",
            nation=nation,
            mode="score",
            scale="65 66",
        )
        try:
            root = await nation
        except NotFound:
            nation = nation["nation"]
            embed = discord.Embed(
                title=nation.replace("_", " ").title(),
                url="https://www.nationstates.net/page="
                "boneyard?nation={}".format("_".join(nation.split()).lower()),
                description="This nation does not exist.",
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            embed.set_thumbnail(url="http://i.imgur.com/Pp1zO19.png")
            return await self._maybe_embed(ctx, embed)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        endo = int(root[".//SCALE[@id='66']/SCORE"])
        if endo == 1:
            endo = "{:d} endorsement".format(endo)
        else:
            endo = "{:d} endorsements".format(endo)
        if root["FOUNDED"] == "0":
            root["FOUNDED"] = "in Antiquity"
        embed = discord.Embed(
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
            colour=await ctx.embed_colour(),
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
            value="{} | {:d} influence ({})".format(
                endo, int(root[".//SCALE[@id='65']/SCORE"]), root["INFLUENCE"]
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
        return await self._maybe_embed(ctx, embed)

    @commands.command()
    @commands.bot_has_permissions(embed_links=True)
    async def region(self, ctx, *, region: link_extract):
        """Retrieves general info about a specified NationStates region"""
        region = Api(
            "delegate delegateauth flag founded founder lastupdate name numnations power",
            region=region,
        )
        try:
            root = await region
        except HTTPException as e:
            if e.status != 404:
                return await ctx.send(f"{e.status}: {e.message}")
            region = region["region"]
            embed = discord.Embed(
                title=region.replace("_", " ").title(), description="This region does not exist."
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            return await self._maybe_embed(ctx, embed)
        if root["DELEGATE"] == "0":
            root["DELEGATE"] = "No Delegate"
        else:
            delroot = await Api(
                "census fullname influence", nation=root["DELEGATE"], scale="65 66", mode="score"
            )
            endo = int(delroot[".//SCALE[@id='66']/SCORE"])
            if endo == 1:
                endo = "{:d} endorsement".format(endo)
            else:
                endo = "{:d} endorsements".format(endo)
            root["DELEGATE"] = (
                "[{}](https://www.nationstates.net/nation={})"
                " | {} | {:d} influence ({})".format(
                    delroot["FULLNAME"],
                    root["DELEGATE"],
                    endo,
                    int(delroot[".//SCALE[@id='65']/SCORE"]),
                    delroot["INFLUENCE"],
                )
            )
        if "X" in root["DELEGATEAUTH"]:
            root["DELEGATEAUTH"] = ""
        else:
            root["DELEGATEAUTH"] = " (Non-Executive)"
        if root["FOUNDED"] == "0":
            root["FOUNDED"] = "in Antiquity"
        if root["FOUNDER"] == "0":
            root["FOUNDER"] = "No Founder"
        else:
            try:
                root["FOUNDER"] = "[{}](https://www.nationstates.net/" "nation={})".format(
                    (await Api("fullname", nation=root["FOUNDER"]))["FULLNAME"], root["FOUNDER"]
                )
            except HTTPException as e:
                if e.status != 404:
                    return await ctx.send(f"{e.status}: {e.message}")
                root["FOUNDER"] = "{} (Ceased to Exist)".format(
                    root["FOUNDER"].replace("_", " ").capitalize()
                )
        embed = discord.Embed(
            title=root["NAME"],
            url="https://www.nationstates.net/region={}".format(root.get("id")),
            description="[{} nations](https://www.nationstates.net/region={}"
            "/page=list_nations) | Founded {} | Power: {}".format(
                root["NUMNATIONS"], root.get("id"), root["FOUNDED"], root["POWER"]
            ),
            timestamp=datetime.utcfromtimestamp(root["LASTUPDATE"]),
            colour=await ctx.embed_colour(),
        )
        embed.set_author(name="NationStates", url="https://www.nationstates.net/")
        if root["FLAG"]:
            embed.set_thumbnail(url=root["FLAG"])
        embed.add_field(name="Founder", value=root["FOUNDER"], inline=False)
        embed.add_field(
            name="Delegate{}".format(root["DELEGATEAUTH"]), value=root["DELEGATE"], inline=False
        )
        embed.set_footer(text="Last Updated")
        await self._maybe_embed(ctx, embed)

    # __________ ASSEMBLY __________

    @commands.command(aliases=["ga", "sc"])
    @commands.bot_has_permissions(embed_links=True)
    async def wa(self, ctx, resolution_id: Optional[int] = None, *options: WAOptions.convert):
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
        options = reduce(lambda x, y: x | y, options) if options else WAOptions.NONE
        if resolution_id and options & (WAOptions.NATION | WAOptions.DELEGATE):
            return await ctx.send(
                "The Nations and Delegates options are not available for past resolutions."
            )
        is_sc = ctx.invoked_with == "sc"
        try:
            request = {"q": ["resolution"], "wa": "2" if is_sc else "1"}
            if options & WAOptions.DELEGATE:
                request["q"].append("delvotes")
            if resolution_id:
                request["id"] = str(resolution_id)
            else:
                request["q"].append("lastresolution")
            root = await Api(request)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        img = "4dHt6si" if is_sc else "7EMYsJ6"
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
            embed = discord.Embed(
                title="Last Resolution", description=out, colour=await ctx.embed_colour()
            )
            embed.set_thumbnail(url="http://i.imgur.com/{}.jpg".format(img))
            return await self._maybe_embed(ctx, embed)
        root = root["RESOLUTION"]
        if options & WAOptions.TEXT:
            description = "**Category: {}**\n\n{}".format(root["CATEGORY"], root["DESC"])
        else:
            description = "Category: {}".format(root["CATEGORY"])
        if len(description) > 2048:
            description = description[:2047] + "\N{HORIZONTAL ELLIPSIS}"
        embed = discord.Embed(
            title=root["NAME"],
            url="https://www.nationstates.net/page={}".format("sc" if is_sc else "ga")
            if not resolution_id
            else "https://www.nationstates.net/page=WA_past_resolution/id={}/council={}".format(
                resolution_id, "2" if is_sc else "1"
            ),
            description=description,
            timestamp=datetime.utcfromtimestamp(
                root["IMPLEMENTED" if resolution_id else "PROMOTED"]
            ),
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
        except HTTPException as e:
            await ctx.send(f"{e.status}: {e.message}")
        else:
            embed.set_author(
                name=authroot["FULLNAME"],
                url="https://www.nationstates.net/nation={}".format(root["PROPOSED_BY"]),
                icon_url=authroot["FLAG"],
            )
        embed.set_thumbnail(url="http://i.imgur.com/{}.jpg".format(img))
        if options & WAOptions.DELEGATE:
            for_del_votes = sorted(
                root.iterfind("DELVOTES_FOR/DELEGATE"), key=lambda e: e["VOTES"], reverse=True
            )[:10]
            against_del_votes = sorted(
                root.iterfind("DELVOTES_AGAINST/DELEGATE"), key=lambda e: e["VOTES"], reverse=True
            )[:10]
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
        if options & WAOptions.VOTE:
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
            )
        if options & WAOptions.NATION:
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
            )
        embed.set_footer(text="Implemented On" if resolution_id else "Voting Began")
        await self._maybe_embed(ctx, embed)

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
        request = {}
        key = "q"
        for i, shard in enumerate(shards):
            if shard.startswith("--"):
                if key != "q":
                    return await ctx.send("No value provided for key {!r}".format(key))
                key = shard[2:]
            elif shard.startswith("*"):
                request.setdefault(key, []).append(" ".join((shard[1:], *shards[i + 1 :])).strip())
                break
            else:
                request.setdefault(key, []).append(shard)
                key = "q"
        if key != "q":
            return await ctx.send("No value provided for key {!r}".format(key))
        try:
            root = await Api(request)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        await ctx.send_interactive(pagify(root.to_pretty_string(), shorten_by=11), "xml")

    # __________ ENDORSE __________

    @commands.command()
    async def ne(self, ctx, *, wa_nation: str):
        """Nations Endorsing (NE) the specified WA nation"""
        try:
            root = await Api("endorsements fullname wa", nation=wa_nation)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        if root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{root['FULLNAME']} is not a WA member.")
        await ctx.send(
            "Nations endorsing " + root["FULLNAME"],
            file=discord.File(BytesIO(root["ENDORSEMENTS"].encode()), "ne.txt"),
        )

    @commands.command()
    async def nec(self, ctx, *, wa_nation: str):
        """Nations Endorsing [Count] (NEC) the specified WA nation"""
        try:
            root = await Api("census fullname wa", nation=wa_nation, scale="66", mode="score")
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
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
        try:
            root = await Api("census fullname", nation=nation, scale="65", mode="score")
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        await ctx.send(
            "{} has {:.0f} influence".format(root["FULLNAME"], root[".//SCALE[@id='65']/SCORE"])
        )

    @commands.command()
    async def nne(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing (NNE) the specified WA nation"""
        try:
            nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        if nation_root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{nation_root['FULLNAMENAME']} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root["REGION"])
        final = (
            set(region_root["NATIONS"].split(":"))
            .intersection(wa_root["MEMBERS"].split(","))
            .difference(nation_root["ENDORSEMENTS"].split(","))
        )
        await ctx.send(
            "Nations not endorsing " + nation_root["FULLNAME"],
            file=discord.File(BytesIO(",".join(final).encode()), "nne.txt"),
        )

    @commands.command()
    async def nnec(self, ctx, *, wa_nation: str):
        """Nations Not Endorsing [Count] (NNEC) the specified WA nation"""
        try:
            nation_root = await Api("endorsements fullname region wa", nation=wa_nation)
        except HTTPException as e:
            return await ctx.send(f"{e.status}: {e.message}")
        if nation_root["UNSTATUS"].lower() == "non-member":
            return await ctx.send(f"{nation_root['NAME']} is not a WA member.")
        wa_root = await Api("members", wa="1")
        region_root = await Api("nations", region=nation_root["REGION"])
        final = (
            set(region_root["NATIONS"].split(":"))
            .intersection(wa_root["MEMBERS"].split(","))
            .difference(nation_root["ENDORSEMENTS"].split(","))
        )
        await ctx.send(
            "{:.0f} nations are not endorsing {}".format(len(final), nation_root["FULLNAME"])
        )
