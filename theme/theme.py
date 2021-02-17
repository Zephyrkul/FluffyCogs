from io import BytesIO
from random import choice

import discord
from redbot.core import Config, commands
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.chat_formatting import bold, italics, pagify, warning
from redbot.core.utils.menus import menu

_ = Translator("Theme", __file__)


def theme_strip(argument):
    return [t.strip().strip('"<>"') for t in argument.split(",")]


@cog_i18n(_)
class Theme(commands.Cog):
    """
    Allows you to set themes to easily play accross all servers.
    """

    async def red_get_data_for_user(self, *, user_id):
        if themes := await self.config.user_from_id(user_id).themes():
            themes_text = "\n".join(themes)
            bio = BytesIO(
                (f"You currently have the following theme songs saved:\n{themes_text}").encode(
                    "utf-8"
                )
            )
            bio.seek(0)
            return {f"{self.__class__.__name__}.txt": bio}
        return {}  # No data to get

    async def red_delete_data_for_user(self, *, requester, user_id):
        # Nothing here is operational, so just delete it all
        await self.config.user_from_id(user_id).clear()

    def __init__(self):
        super().__init__()
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_user(themes=[])

    @commands.group(invoke_without_command=True, aliases=["themes"])
    @commands.guild_only()
    async def theme(self, ctx, *, user: discord.User = None):
        """
        Play, view, or configure a user's set theme song(s).
        """
        if not ctx.invoked_subcommand:
            await ctx.invoke(self.theme_play, user=user)

    @theme.command(name="play")
    @commands.guild_only()
    async def theme_play(self, ctx, *, user: discord.User = None):
        """
        Play a user's set theme song(s).
        """
        play = ctx.bot.get_command("play")
        if not play:
            return await ctx.send(warning(_("Audio cog is not loaded.")))
        if not user:
            user = ctx.author
        themes = await self.maybe_bot_themes(ctx, user)
        if not themes:
            return await ctx.send(_("{} has not set any themes.").format(user.name))
        theme = choice(themes)
        await ctx.invoke(play, query=theme)

    @theme.command(name="add")
    async def theme_add(self, ctx, *, new_themes: theme_strip):
        """
        Adds the specified themes to your theme list.

        Comma-seperated list.
        """
        async with self.config.user(ctx.author).themes() as themes:
            themes[:] = set(themes).union(new_themes)
        await ctx.send(_("Themes added."))

    @theme.command(name="remove")
    async def theme_remove(self, ctx, *, themes_to_remove: theme_strip):
        """
        Removes the specified themes from your theme list.

        Comma-seperated list.
        """
        async with self.config.user(ctx.author).themes() as themes:
            if not themes:
                return await ctx.send(_("You have no themes to remove."))
            themes[:] = set(themes).difference(themes_to_remove)
        await ctx.send(_("Themes removed."))

    @theme.command(name="clear")
    async def theme_clear(self, ctx):
        """
        Clear your list of themes.

        \N{WARNING SIGN} This action cannot be undone.
        """
        if not await self.config.user(ctx.author).themes():
            return await ctx.send(_("You have no themes to remove."))

        async def clear(ctx, pages, controls, message, *_):
            try:
                await message.clear_reactions()
            except discord.Forbidden:
                for key in controls.keys():
                    await message.remove_reaction(key, ctx.bot.user)

        async def yes(*args):
            # pylint: disable=E1120
            await clear(*args)
            return True

        async def no(*args):
            # pylint: disable=E1120
            await clear(*args)
            return False

        reply = await menu(
            ctx,
            [_("Are you sure you wish to clear your themes?")],
            {"\N{WHITE HEAVY CHECK MARK}": yes, "\N{CROSS MARK}": no},
        )
        if reply:
            await self.config.user(ctx.author).clear()
            await ctx.send(_("Themes cleared."))
        else:
            await ctx.send(_("Okay, I haven't cleared your themes."))

    @theme.command(name="list")
    async def theme_list(self, ctx, *, user: discord.User = None):
        """
        Lists your currently set themes.
        """
        if not user:
            user = ctx.author
        themes = await self.maybe_bot_themes(ctx, user)
        if themes:
            message = self.pretty_themes(bold(_("{}'s Themes")).format(user.name), themes)
        else:
            message = "{}\n\n{}".format(
                bold(_("{0}'s Themes")), italics(_("{0} has not set any themes."))
            ).format(user.name)
        for msg in pagify(message):
            await ctx.maybe_send_embed(msg)

    async def maybe_bot_themes(self, ctx, user):
        if user == ctx.bot.user:
            return (
                "https://youtu.be/zGTkAVsrfg8",
                "https://youtu.be/cGMWL8cOeAU",
                "https://youtu.be/vFrjMq4aL-g",
                "https://youtu.be/WROI5WYBU_A",
                "https://youtu.be/41tIUr_ex3g",
                "https://youtu.be/f9O2Rjn1azc",
            )
        elif user.bot:
            return ("https://youtu.be/nMyoI-Za6z8",)
        else:
            return await self.config.user(user).themes()

    def pretty_themes(self, pre, themes):
        themes = "\n".join(f"<{theme}>" for theme in themes)
        return f"{pre}\n\n{themes}"
