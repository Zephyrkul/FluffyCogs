import asyncio
import discord
from typing import Dict, Tuple, List

from redbot.core import commands, checks
from redbot.core.utils.mod import get_audit_reason
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate


class MemberRestore(commands.Cog):
    def __init__(self):
        super().__init__()
        self.cache: Dict[discord.Member, Tuple[str, List[discord.Role]]] = {}

    @checks.admin_or_permissions(kick_members=True, manage_roles=True, manage_nicknames=True)
    @commands.bot_has_permissions(embed_links=True)
    @commands.command()
    async def restore_member(self, ctx, *, member: discord.Member):
        """
        Restores a member's nickname and roles from before they were last removed from the server.

        Data is stored only in memory, and will be discarded whenever this cog is unloaded.
        """
        if member not in self.cache:
            return await ctx.send(f"Member {member} is not in my cache.")
        nick, roles = self.cache[member]
        roles = list(
            filter(
                lambda r: (
                    r
                    and r < ctx.bot.top_role
                    and r < ctx.author.top_role
                    and r != r.guild.default_role
                ),
                map(ctx.guild.get_role, roles),
            )
        )
        roles.extend(r for r in member.roles if r != r.guild.default_role)
        if not nick and not roles:
            return await ctx.send(
                f"Member {member} had no nickname or roles when they were last removed from the server."
            )
        embed = discord.Embed(colour=await ctx.embed_colour())
        embed.add_field(name="Nickname", value=nick if nick else "*None*")
        embed.add_field(name="Roles", value="\n".join(r.mention for r in roles))
        menu = await ctx.send(
            content=(
                f"The following will be reapplied to {member}.\n"
                "Are you sure you want to apply the following?"
            ),
            embed=embed,
        )
        start_adding_reactions(menu, ReactionPredicate.YES_OR_NO_EMOJIS)
        predicate = ReactionPredicate.yes_or_no(message=menu, user=ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=predicate, timeout=60)
        except asyncio.TimeoutError:
            pass
        if not predicate.result:
            return await ctx.send(f"Alright, then, I've left {member}'s roles alone.")
        await member.edit(nick=nick, roles=roles, reason=get_audit_reason(ctx.author))
        await ctx.send("Done.")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.cache[member] = member.nick, list(map(lambda r: r.id, member.roles))
