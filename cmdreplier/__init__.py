from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red
    from redbot.core.commands import Context

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]


async def new_send(__sender, /, *args, **kwargs):
    ctx: Context = __sender.__self__
    if not ctx.command_failed and "reference" not in kwargs:
        message = ctx.message
        try:
            resolved = message.reference.resolved
            failsafe_ref = resolved.to_reference(fail_if_not_exists=False)
        except AttributeError:
            pass
        else:
            kwargs["reference"] = failsafe_ref
            kwargs["mention_author"] = resolved.author in message.mentions
    return await __sender(*args, **kwargs)


async def before_hook(ctx: Context):
    # onedit allows command calls on message edits
    # since replies always mention and can't be changed on edit,
    # this won't patch ctx if the command invokation is an edit.
    if ctx.message.reference and not ctx.message.edited_at:
        try:
            # before_hook might be called multiple times
            # clear any overwritten send method before overwriting it again
            del ctx.send
        except AttributeError:
            pass
        ctx.send = partial(new_send, ctx.send)


def setup(bot: Red):
    bot.before_invoke(before_hook)


def teardown(bot: Red):
    bot.remove_before_invoke_hook(before_hook)
