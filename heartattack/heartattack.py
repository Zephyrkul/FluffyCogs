import discord
import logging
import sys
import threading
import traceback

from redbot.core import commands


dpy_log = logging.getLogger("discord.gateway")
ha_log = logging.getLogger("red.heartattack")


class HA_Handler(logging.Handler):
    @staticmethod
    def ha_filter(record):
        return "heartbeat" in record.msg.lower()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addFilter(self.ha_filter)

    def createLock(self):
        self.lock = None  # No lock required; do not create one

    def emit(self, record):
        stack = sys._current_frames()[threading.main_thread().ident]
        stack = traceback.format_stack(stack)
        ha_log.warning(
            f"{record.msg}\nMain thread stack (most recent call last):\n%s",
            *record.args,
            "".join(stack),
        )


class HeartAttack(commands.Cog):
    def __init__(self):
        self.handler = HA_Handler()
        dpy_log.addHandler(self.handler)

    def __unload(self):
        dpy_log.removeHandler(self.handler)

    __del__ = __unload

    cog_unload = __unload
