import aiohttp
import contextlib
import discord
import re
from datetime import datetime
from html import unescape
from lxml import etree

from redbot.core import checks, commands, Config, version_info as red_version
from redbot.core.utils.chat_formatting import box, pagify

from . import api
from .api import Nation, Region, World, WA
from .nsautorole import task


Cog = getattr(commands, "Cog", object)


def valid_api(argument):
    return {
        "n": Nation,
        "nation": Nation,
        "r": Region,
        "region": Region,
        "w": World,
        "world": World,
        "wa": WA,
    }[argument.lower()]()


def task_running():
    def predicate(ctx):
        ns = ctx.bot.get_cog(NationStates.__name__)
        return ns and ns.nsartask

    return commands.check(predicate)


class NationStates(Cog):
    def __init__(self, bot):
        api.start(bot.loop)
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(agent=None, data_cache=None, data_updated=None)
        self.config.register_guild(
            region=None,
            roles=dict.fromkeys(("ex_nations", "residents", "visitors", "wa_residents"), None),
        )
        self.nsartask = None

    async def initialize(self):
        agent = await self.config.agent()
        if not agent:
            if not self.bot.owner_id:
                # always False but forces owner_id to be filled
                await self.bot.is_owner(discord.Object(id=None))
            owner_id = self.bot.owner_id
            # only make the user_info request if necessary
            agent = str(self.bot.get_user(owner_id) or await self.bot.get_user_info(owner_id))
        api.agent(f"{agent} redbot/{red_version}")

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
            num = num / 1e3
        return "{} {}".format(round(num, 3), illion[index])

    @commands.command()
    @commands.cooldown(2, 3600)
    @checks.is_owner()
    async def agent(self, ctx, *, agent: str):
        new_agent = api.agent(f"{agent} redbot/{red_version}")
        await self.config.agent.set(agent)
        await ctx.send(f"Agent set: {new_agent}")

    @commands.command()
    @commands.bot_has_permissions(embed_links=True)
    async def nation(
        self,
        ctx,
        *,
        nation: Nation.category.census(
            mode="score", scale="65 66"
        ).demonym2plural.flag.founded.freedom.fullname.influence.lastlogin.motto.population.region.wa,
    ):
        try:
            root = await nation
        except aiohttp.ClientResponseError as e:
            if e.status != 404:
                return await ctx.send(f"{e.status}: {e.message}")
            nation = nation.value
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
                Region(root["REGION"]).value,
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
    async def region(
        self,
        ctx,
        *,
        region: Region.delegate.delegateauth.flag.founded.founder.lastupdate.name.numnations.power,
    ):
        try:
            root = await region
        except aiohttp.ClientResponseError as e:
            if e.status != 404:
                return await ctx.send(f"{e.status}: {e.message}")
            region = region.value
            embed = discord.Embed(
                title=region.replace("_", " ").title(), description="This region does not exist."
            )
            embed.set_author(name="NationStates", url="https://www.nationstates.net/")
            return await self._maybe_embed(ctx, embed)
        if root["DELEGATE"] == "0":
            root["DELEGATE"] = "No Delegate"
        else:
            delroot = (
                await Nation(root["DELEGATE"])
                .census(scale="65 66", mode="score")
                .fullname.influence
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
                    (await Nation(root["FOUNDER"]).fullname)["FULLNAME"], root["FOUNDER"]
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
        wa = WA(ctx.invoked_with).resolution.delvotes.lastresolution
        try:
            root = await wa
        except aiohttp.ClientResponseError as e:
            return await ctx.send(f"{e.status}: {e.message}")
        img = "4dHt6si" if wa.value == "2" else "7EMYsJ6"
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
        authroot = await Nation(root["PROPOSED_BY"]).fullname.flag
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
    async def shard(self, ctx, ns: valid_api, target: str, *shards: str):
        try:
            ns(target)
        except TypeError:
            shards = (target, *shards)
        try:
            root = await (ns + shards)
        except aiohttp.ClientResponseError as e:
            return await ctx.send(f"{e.status}: {e.message}")
        await ctx.send_interactive(
            pagify(etree.tostring(root, encoding=str, pretty_print=True), shorten_by=11), "xml"
        )

    def __unload(self):
        with contextlib.suppress(Exception):
            api.close()

    __del__ = __unload
