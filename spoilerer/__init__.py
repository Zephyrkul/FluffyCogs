import json
from pathlib import Path

from .spoilerer import Spoilerer

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]


async def setup(bot):
    spoilerer = Spoilerer(bot)
    await spoilerer.initialize()
    bot.add_cog(spoilerer)
