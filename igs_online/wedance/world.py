"""World events and other server-wide settings (data/world.json, invented
defaults; `--world file` replaces it).

- feature: g_wFeature, sent in the login reply (0x12) and every profile.
  Bits seen in the game: 0x0001 hides the group page's missions, achievements
  and schedule entries and turns off plus items, 0x0002, 0x0008, 0x0400 (set
  by the game itself), 0x1000 / 0x2000 free play (halved rewards, unless
  VIP; 0x2000 also when the cabinet is on free play); 0xFFFF: every online
  feature closed ("feature closed" in the attract).
- world_seed, world_level: the world tour's (世界巡迴賽) map: the seed of its
  random rule and star layout (worldrule.lua SetSRandSeedAndWorldTable) and
  its table, 0 easy, 1 normal, 2 hard.  "weekly" or "daily" derive a new seed
  each week or day.
- exp: the activity rate (EXP multiplier in percent, 100..1000) sent in the
  profile and in 0x162: `base`, raised to `rate` inside each window
  (`days`: 0 Monday .. 6 Sunday, `from` / `to` HH:MM, local time).
- conditions: 0x16A entries (type, text).  The game parses the first as
  "[id,start,end,a,b,rate]..." exp bonuses and keeps the rest; it shows none
  of them.
- ticker, notice: 0x166, 0x168.
- ping_test, signup_reminder: login reply flags (ICMP ping test of
  info/ping_data.lst, reported in 0x154; the Acropass sign-up reminder).
"""
import datetime
import json
import os

from . import gamedata

DEFAULTS = {
    "feature": 0,
    "world_seed": "weekly",
    "world_level": 1,
    "ping_test": 0,
    "signup_reminder": 0,
    "exp": {"base": 100, "windows": []},
    "conditions": [],
    "ticker": "",
    "notice": "",
}


class World:
    def __init__(self, path=None):
        cfg = dict(DEFAULTS)
        src = path or os.path.join(gamedata.DATA, "world.json")
        if os.path.exists(src):
            with open(src, encoding="utf-8") as f:
                cfg.update(json.load(f))
        self.cfg = cfg

    def __getitem__(self, k):
        return self.cfg[k]

    def seed(self, now=None):
        s = self.cfg["world_seed"]
        now = now or datetime.datetime.now()
        if s == "weekly":
            y, w, _ = now.isocalendar()
            return (y * 53 + w) & 0xFFFF
        if s == "daily":
            return now.toordinal() & 0xFFFF
        return int(s) & 0xFFFF

    def level(self):
        return int(self.cfg["world_level"]) & 0xFF

    def activity_rate(self, now=None):
        now = now or datetime.datetime.now()
        exp = self.cfg["exp"]
        rate = int(exp.get("base", 100))
        hm = now.strftime("%H:%M")
        for w in exp.get("windows", []):
            if now.weekday() in w.get("days", range(7)) and w.get("from", "00:00") <= hm <= w.get("to", "23:59"):
                rate = max(rate, int(w["rate"]))
        return max(100, min(1000, rate))

    def conditions(self):
        return [(int(c["type"]), c["text"]) for c in self.cfg["conditions"]]
