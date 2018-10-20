import aiohttp
import contextlib
import discord
import re
from datetime import datetime
from html import unescape
from lxml import etree

from redbot.core import checks, commands, Config, version_info as red_version
from redbot.core.utils.chat_formatting import box, pagify

from .api import Api, link_extract, wait_if


Cog = getattr(commands, "Cog", object)
eq = re.compile(r"\s*=\s*")


def maybe_link(link: str):
    match = link_extract(link)
    return match[1] if match else link


class NationStates(Cog):
    def __init__(self, bot):
        Api.start(bot.loop)
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
        Api.agent = f"{agent} redbot/{red_version}"

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

    @commands.command()
    @commands.cooldown(2, 3600)
    @checks.is_owner()
    async def agent(self, ctx, *, agent: str):
        """
        Sets the user agent.
        
        Recommendations: https://www.nationstates.net/pages/api.html#terms
        Defaults to your username#hash
        """
        Api.agent = f"{agent} redbot/{red_version}"
        await self.config.agent.set(agent)
        await ctx.send(f"Agent set: {Api.agent}")

    @commands.command()
    @commands.bot_has_permissions(embed_links=True)
    async def nation(self, ctx, *, nation: maybe_link):
        """Retrieves general info about a specified NationStates nation"""
        nation = Api(
            "census category demonym2plural flag founded freedom fullname influence lastlogin motto population region wa",
            nation=nation,
            mode="score",
            scale="65 66",
        )
        try:
            root = await nation
        except aiohttp.ClientResponseError as e:
            if e.status != 404:
                return await ctx.send(f"{e.status}: {e.message}")
            nation = nation["nation"]
            embed = discord.Embed(
                title=nation.replace("_", " ").title(),
                url="https://www.nationstates.net/page="
                "boneyard?nation={}".format(nation.replace(" ", "_").lower()),
                description="This nation does not exist.",
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            embed.set_thumbnail(url="http://i.imgur.com/Pp1zO19.png")
            return await self._maybe_embed(ctx, embed)
        endo = int(float(root[".//SCALE[@id='66']/SCORE"]))
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
            timestamp=datetime.utcfromtimestamp(float(root["LASTLOGIN"])),
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
                endo, int(float(root[".//SCALE[@id='65']/SCORE"])), root["INFLUENCE"]
            ),
            inline=False,
        )
        embed.set_footer(text="Last Active")
        return await self._maybe_embed(ctx, embed)

    @commands.command()
    @commands.bot_has_permissions(embed_links=True)
    async def region(self, ctx, *, region: maybe_link):
        """Retrieves general info about a specified NationStates region"""
        region = Api(
            "delegate delegateauth flag founded founder lastupdate name numnations power",
            region=region,
        )
        try:
            root = await region
        except aiohttp.ClientResponseError as e:
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
            endo = int(float(delroot[".//SCALE[@id='66']/SCORE"]))
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
                    int(float(delroot[".//SCALE[@id='65']/SCORE"])),
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
            except aiohttp.ClientResponseError as e:
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
            timestamp=datetime.utcfromtimestamp(float(root["LASTUPDATE"])),
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

    @commands.command(aliases=["ga", "sc"])
    @commands.bot_has_permissions(embed_links=True)
    async def wa(self, ctx):
        """
        Retrieves general info about the World Assembly
        
        Defaults to the General Assembly. Use [p]sc to get info about the Security Council.
        """
        is_sc = ctx.invoked_with == "sc"
        try:
            root = await Api("resolution delvotes lastresolution", wa="2" if is_sc else "1")
        except aiohttp.ClientResponseError as e:
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
        for_votes = sorted(
            root.iterfind("DELVOTES_FOR/DELEGATE"), key=lambda e: int(e["VOTES"]), reverse=True
        )[:10]
        against_votes = sorted(
            root.iterfind("DELVOTES_AGAINST/DELEGATE"), key=lambda e: int(e["VOTES"]), reverse=True
        )[:10]
        embed = discord.Embed(
            title=root["NAME"],
            url="https://www.nationstates.net/page={}".format(ctx.invoked_with),
            description="Category: {}".format(root["CATEGORY"]),
            timestamp=datetime.utcfromtimestamp(float(root["PROMOTED"])),
            colour=await ctx.embed_colour(),
        )
        authroot = await Api("fullname flag", nation=root["PROPOSED_BY"])
        embed.set_author(
            name=authroot["FULLNAME"],
            url="https://www.nationstates.net/nation={}".format(root["PROPOSED_BY"]),
            icon_url=authroot["FLAG"],
        )
        embed.set_thumbnail(url="http://i.imgur.com/{}.jpg".format(img))
        embed.add_field(
            name="Top Delegates For",
            value="\t|\t".join(
                "[{}](https://www.nationstates.net/nation={}) ({})".format(
                    e["NATION"].replace("_", " ").title(), e["NATION"], e["VOTES"]
                )
                for e in for_votes
            ),
            inline=False,
        )
        embed.add_field(
            name="Top Delegates Against",
            value="\t|\t".join(
                "[{}](https://www.nationstates.net/nation={}) ({})".format(
                    e["NATION"].replace("_", " ").title(), e["NATION"], e["VOTES"]
                )
                for e in against_votes
            ),
            inline=False,
        )
        percent = (
            100
            * float(root["TOTAL_VOTES_FOR"])
            / (float(root["TOTAL_VOTES_FOR"]) + float(root["TOTAL_VOTES_AGAINST"]))
        )
        embed.add_field(
            name="Total Votes",
            value="For {}\t{:◄<13}\t{} Against".format(
                root["TOTAL_VOTES_FOR"],
                "►" * int(round(percent / 10)) + str(int(round(percent))) + "%",
                root["TOTAL_VOTES_AGAINST"],
            ),
        )
        embed.set_footer(text="Voting Began")
        await self._maybe_embed(ctx, embed)

    @commands.command()
    async def shard(self, ctx, *shards: str):
        """
        Retrieves the specified info from NationStates

        Uses UNIX-style arguments. Arguments will be shards, while flags will be keywords.
        Examples:
            [p]shard --nation Darcania census --scale "65 66" --mode score
            [p]shard numnations lastupdate delegate --region "10000 Islands"
        """
        request = {}
        key = "q"
        for shard in shards:
            if shard.startswith("--"):
                key = shard[2:]
            else:
                request.setdefault(key, []).append(shard)
                key = "q"
        try:
            root = await Api(**request)
        except aiohttp.ClientResponseError as e:
            return await ctx.send(f"{e.status}: {e.message}")
        await ctx.send_interactive(
            pagify(etree.tostring(root, encoding=str, pretty_print=True), shorten_by=11), "xml"
        )

    def __unload(self):
        with contextlib.suppress(Exception):
            Api.close()

    __del__ = __unload
