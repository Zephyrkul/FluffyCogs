import json
from pathlib import Path

from .rtfs import RTFS

with open(Path(__file__).parent / "info.json", encoding="UTF-8") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]


def setup(bot):
    bot.add_cog(RTFS(bot))
