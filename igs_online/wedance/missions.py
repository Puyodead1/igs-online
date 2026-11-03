"""Missions: one open mission per member, sent as the profile's mission
block, played with a mission ticket (任務門票, a market ticket), reported
with 0x26E, checked again with 0x161 -> 0x162.

The set is data/missions.json (invented).  A member gets the first mission
not yet passed; when the last is passed the list starts over (passing again
gives the bonus crystals and coins instead of the song).  Progress comes
from the game (0x26E carries both counters); passing unlocks the song.

p["mission_state"] = {"id", "progress_a", "progress_b", "start", "end"};
p["missions_passed"] = [ids].
"""
import time

from . import gamedata

DAY = 86400


class Missions:
    def __init__(self, data=None):
        self.data = data or gamedata.load("missions.json")
        self.by_id = {m["id"]: m for m in self.data["missions"]}

    def next_for(self, p):
        done = set(p.get("missions_passed", []))
        for m in self.data["missions"]:
            if m["id"] not in done:
                return m
        return self.data["missions"][0] if self.data["missions"] else None

    def current(self, p, now=None):
        """the member's open mission state, starting a new one when needed"""
        now = int(now or time.time())
        st = p.get("mission_state")
        if st and st.get("id") in self.by_id and st.get("end", 0) > now:
            return st
        m = self.next_for(p)
        if not m:
            p.pop("mission_state", None)
            return None
        st = {"id": m["id"], "progress_a": 0, "progress_b": 0, "start": now, "end": now + m.get("days", 30) * DAY}
        p["mission_state"] = st
        return st

    def block(self, p, now=None):
        """(mission id, 36-byte block fields) for the profile"""
        st = self.current(p, now)
        if not st:
            return 0, None
        m = self.by_id[st["id"]]
        a = m.get("cond_a") or [0, 0]
        b = m.get("cond_b") or [0, 0]
        return st["id"], {
            "song": m["song"], "cond_a": a[0], "cond_b": b[0], "target_a": a[1], "target_b": b[1],
            "progress_a": st["progress_a"], "progress_b": st["progress_b"],
            "bonus_crystal": m.get("bonus_crystal", 0), "bonus_coin": m.get("bonus_coin", 0),
            "start": st["start"], "end": st["end"],
        }

    def valid(self, p, mid, now=None):
        st = p.get("mission_state")
        return bool(st and st.get("id") == mid and st.get("end", 0) > int(now or time.time()))

    def result(self, p, mid, passed, song, a, b):
        """0x26E: (i32 id, u8 passed, u16 song, i32 progress a, i32 progress b); returns a log text"""
        st = p.get("mission_state")
        if not st or st.get("id") != mid:
            return "mission %d is not open" % mid
        if passed:
            p.setdefault("missions_passed", [])
            if mid not in p["missions_passed"]:
                p["missions_passed"].append(mid)
            if song and song not in p["songs"]:
                p["songs"].append(song)
            p.pop("mission_state", None)
            return "mission %d passed, song %d" % (mid, song)
        st["progress_a"], st["progress_b"] = a, b
        return "mission %d: %d / %d" % (mid, a, b)
