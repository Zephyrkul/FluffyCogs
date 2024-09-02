from typing import Union

import discord
from redbot.core import app_commands
from redbot.core.bot import Red


@app_commands.context_menu(name="Avatar", extras={"red_force_enable": True})
@app_commands.user_install()
async def avatar(interaction: discord.Interaction[Red], user: Union[discord.Member, discord.User]):
    await interaction.response.send_message(
        embed=discord.Embed(title=f"{user.display_name} - {user.id}", color=user.color).set_image(
            url=user.display_avatar.url
        ),
        ephemeral=True,
    )


async def setup(bot: Red):
    bot.tree.add_command(avatar)
