import logging
import sys
import threading
import traceback

import discord
from redbot.core.errors import CogLoadError

dpy_log = logging.getLogger("discord.gateway")
ha_log = logging.getLogger("red.fluffy.heartattack")


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


ha_handler = HA_Handler()


def setup(bot):
    if discord.version_info >= (1, 4):
        raise CogLoadError("Heartattack is no longer necessary in d.py 1.4+")
    dpy_log.addHandler(ha_handler)


def teardown(bot):
    dpy_log.removeHandler(ha_handler)
