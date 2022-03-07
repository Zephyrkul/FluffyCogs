from typing import Optional

from .utils import Thinking, contexts


class InterChannel:
    __slots__ = ()

    async def send(self, *args, **kwargs):
        ctx = contexts.get()
        interaction = ctx.interaction
        await self.trigger_typing(ephemeral=kwargs.pop("ephemeral", None))
        delete_after = kwargs.pop("delete_after", None)
        kwargs["wait"] = True
        m = await interaction.followup.send(*args, **kwargs)
        if delete_after is not None:
            await m.delete(delay=delete_after)
        return m

    async def trigger_typing(self, *, ephemeral: Optional[bool] = None) -> None:
        ctx = contexts.get()
        if not ctx._deferred:
            ctx._deferred = True
            if ephemeral is None:
                ephemeral = ctx.command_failed or not ctx.command
            await ctx.interaction.response.defer(ephemeral=ephemeral)

    def typing(self):
        return Thinking(self)
