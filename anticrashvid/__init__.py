# The file bundled with this cog is a base85-encoded list of known hashes that crash discord,
# as pre-computed by trusted sources.

import json
from pathlib import Path

from .anticrashvid import AntiCrashVid

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]


async def setup(bot):
    await bot.add_cog(AntiCrashVid(bot))
