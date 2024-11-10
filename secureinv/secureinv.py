import asyncio
from typing import Optional, TypedDict, Union, cast
from typing_extensions import TypeAlias

import discord
from redbot.core import Config, commands
from redbot.core.utils.mod import get_audit_reason, is_mod_or_superior

InviteableChannel: TypeAlias = Union[
    discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.StageChannel
]  # Categories also have a .create_invite() method, but the API doesn't actually support it


class Settings(TypedDict):
    channel: Optional[int]
    days: Optional[int]
    uses: Optional[int]


class SettingsConverter(commands.FlagConverter, case_insensitive=True, delimiter=" "):
    channel: Optional[InviteableChannel] = None
    days: Optional[commands.Range[int, 0, 7]] = None
    uses: Optional[commands.Range[int, 0, 100]] = None


class InviteSettingsConverter(commands.FlagConverter, case_insensitive=True, delimiter=" "):
    channel: Optional[InviteableChannel] = None
    days: Optional[commands.Range[int, 0, 7]] = None
    uses: Optional[commands.Range[int, 0, 100]] = None
    amount: Optional[commands.Range[int, 1, 10]] = None
    reason: Optional[str] = None


assert frozenset(SettingsConverter.__annotations__) == frozenset(Settings.__annotations__)
assert frozenset(InviteSettingsConverter.__annotations__) >= frozenset(Settings.__annotations__)


@commands.permissions_check
def _record_permissions_checked(ctx):
    """Remember whether permissions was checked, for in-command checks"""
    ctx.__is_permissions_checked__ = True
    return True


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
        self.config.register_guild(**Settings(channel=None, days=1, uses=0))

    @commands.group(invoke_without_command=True)
    @_record_permissions_checked
    async def inv(self, ctx: commands.GuildContext, *, settings: InviteSettingsConverter):
        """
        Create one or several invites with the specified parameters, e.g.
        [p]inv channel #general days 1 uses 6 amount 3 reason "friend group invites"

        For specifying unlimited days or uses, use 0.

        Defaults can be set with `[p]inv set`.
        If no defaults are found, channel defaults to the current channel,
        days defaults to 1, uses defaults to 0 (infinite), and amount defaults to 1.

        Uses will always be finite if days is infinite.
        """
        defaults = cast(Settings, await self.config.guild(ctx.guild).all())
        parent = cast(InviteableChannel, getattr(ctx.channel, "parent", ctx.channel))
        channel = settings.channel
        if not channel and defaults["channel"]:
            channel = cast(Optional[InviteableChannel], ctx.guild.get_channel(defaults["channel"]))
        channel = channel or parent

        # Bot permissions check
        if not channel.permissions_for(ctx.me).create_instant_invite:
            raise commands.BotMissingPermissions(discord.Permissions(create_instant_invite=True))

        # Author permissions check, taking into account bot-mod and permissions checks as well
        # Since this depends on the channel argument, a check decorator won't work
        if not (
            channel.permissions_for(ctx.author).create_instant_invite
            or await is_mod_or_superior(ctx.bot, ctx.author)
            or channel.id == (defaults["channel"] or parent.id)
            and not hasattr(ctx, "__is_permissions_checked__")
        ):
            raise commands.MissingPermissions(["create_instant_invite"])

        days = defaults["days"] if settings.days is None else settings.days
        uses = defaults["uses"] if settings.uses is None else settings.uses
        if days == 0:
            uses = uses or 1  # if days is infinite, limit uses
        generated = await asyncio.gather(
            *(
                channel.create_invite(
                    max_age=(days or 0) * 86400,
                    max_uses=uses or 0,
                    temporary=False,
                    unique=True,
                    reason=get_audit_reason(ctx.author, reason=settings.reason),  # type: ignore
                )
                for _ in range(settings.amount or 1)
            )
        )
        await ctx.send("\n".join(invite.url for invite in generated), delete_after=120)

    @inv.group(name="set", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def _inv_set(self, ctx: commands.GuildContext, *, settings: Optional[SettingsConverter]):
        """
        Configure or view the server's default inv settings, e.g.
        [p]inv set channel #general days 0 uses 1
        """
        if settings is None:
            await ctx.send_help()
            await ctx.maybe_send_embed(
                "\n".join(
                    f"**{k.title()}:** {v}"
                    for k, v in (await self.config.guild(ctx.guild).all()).items()
                )
            )
        else:
            async with self.config.guild(ctx.guild).all() as current_settings:
                for setting, value in settings:
                    if value is not None:
                        current_settings[setting] = value
            await ctx.tick()
