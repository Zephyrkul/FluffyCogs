import asyncio
import numbers
from typing import Generic, Optional, Type, TypeVar, Union

import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.mod import get_audit_reason

T = TypeVar("T", float, int)
defaults = dict(channel=None, days=1, uses=0)


class Dict(commands.get_dict_converter(*defaults.keys())):
    pass


class NonNegative(numbers.Real, Generic[T]):
    @classmethod
    def __class_getitem__(cls, item: Type[T]):
        def inner(argument: str):
            arg: T = item(argument)
            real = getattr(arg, "real", arg)
            if real < 0:
                raise ValueError
            return real

        return inner


class SecureInv(commands.Cog):
    async def red_get_data_for_user(self, *, user_id):
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        pass  # No data to delete

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.last_purge = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_guild(**defaults)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.mod_or_permissions(create_instant_invite=True)
    async def inv(
        self,
        ctx,
        channel: Optional[Union[discord.TextChannel, discord.VoiceChannel]] = None,
        days: Optional[NonNegative[float]] = None,
        uses: Optional[NonNegative[int]] = None,
        amount: Optional[NonNegative[int]] = None,
        *,
        reason: str = None,
    ):
        """
        Create one or several invites with the specified parameters.

        For specifying unlimited days or uses, use 0.

        Defaults can be set with `[p]inv set`.
        If no defaults are found, channel defaults to the current channel,
        days defaults to 1, and uses defaults to 0 (infinite).

        Uses will always be finite if days is infinite.
        """
        settings = await self.config.guild(ctx.guild).all()
        if not channel:
            channel = ctx.guild.get_channel(settings["channel"])
        channel = channel or ctx.channel
        if not channel.permissions_for(ctx.me).create_instant_invite:
            raise commands.BotMissingPermissions(discord.Permissions(create_instant_invite=True))
        if not channel.permissions_for(ctx.author).create_instant_invite:
            raise commands.MissingPermissions(discord.Permissions(create_instant_invite=True))
        if days is None:
            days = settings["days"]
        if uses is None:
            uses = settings["uses"] if days else settings["uses"] or 1
        generated = [
            await channel.create_invite(
                max_age=(days or 0) * 86400,
                max_uses=uses,
                temporary=False,
                unique=True,
                reason=get_audit_reason(ctx.author, reason=reason),
            )
            for _ in range(amount or 1)
        ]
        await ctx.send("\n".join(invite.url for invite in generated), delete_after=120)

    @inv.group(name="set", invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def _inv_set(self, ctx):
        """
        Configure or view the server's default inv settings.
        """
        if not ctx.invoked_subcommand:
            settings = await self.config.guild(ctx.guild).all()
            await asyncio.gather(
                ctx.send_help(),
                ctx.maybe_send_embed(
                    "\n".join(f"**{k.title()}:** {v}" for k, v in settings.items())
                ),
            )

    @_inv_set.command(name="channel")
    async def _set_channel(
        self, ctx, *, channel: Union[discord.TextChannel, discord.VoiceChannel] = None
    ):
        """
        Set or clear the default channel an `[p]inv` directs to.
        """
        if channel is None:
            await self.config.guild(ctx.guild).channel.clear()
        else:
            await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.tick()

    @_inv_set.command(name="days")
    async def _set_days(self, ctx, *, days: NonNegative[float] = None):
        """
        Set or clear the default amount of days an `[p]inv` lasts for.

        Set to 0 for unlimited days.
        """
        if days is None:
            await self.config.guild(ctx.guild).days.clear()
        else:
            await self.config.guild(ctx.guild).days.set(days)
        await ctx.tick()

    @_inv_set.command(name="uses")
    async def _set_uses(self, ctx, *, uses: NonNegative[int] = None):
        """
        Set or clear the default amount of times an `[p]inv` can be used.

        Set to 0 for unlimited uses.
        """
        if uses is None:
            await self.config.guild(ctx.guild).uses.clear()
        else:
            await self.config.guild(ctx.guild).uses.set(uses)
        await ctx.tick()
