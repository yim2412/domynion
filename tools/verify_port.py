"""이식 대조 — 원본이 실제로 낸 값과 우리 값을 맞춰 본다.

`tools/oracle.mts` 가 **원본 TypeScript 를 실행해** 기준값을 JSON 으로 뽑고, 이 도구가
같은 입력을 우리 Python 에 넣어 대조한다. "코드를 눈으로 봤다"가 아니라 **같은 입력에
같은 출력이 나오는가**를 보는 것이 이식 검증의 유일한 근거다.

    # 1) 원본 리포에서 기준값을 뽑는다 (tsx 필요 — 원본 리포에 npm i tsx)
    cd <원본> && ./node_modules/.bin/tsx <이_리포>/tools/oracle.mts <원본> > oracle.json

    # 2) 대조한다
    python tools/verify_port.py oracle.json

불일치가 하나라도 있으면 종료 코드 1.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domynion.core import constants as C            # noqa: E402
from domynion.core.attack import attack_logic, tiles_per_tick   # noqa: E402
from domynion.core.constants import Terrain          # noqa: E402
from domynion.core.doomsday import required_tiles   # noqa: E402
from domynion.core.gamemap import GameMap            # noqa: E402
from domynion.core.naval import (trade_gold, trade_spawn_rate)   # noqa: E402
from domynion.core.nukes import (NUKE_MAGNITUDES, NUKE_SPEED,    # noqa: E402
                                 death_factor, sam_range)
from domynion.core.rail import train_gold, train_spawn_rate      # noqa: E402
from domynion.core.state import PlayerState         # noqa: E402
from domynion.core.units import UNIT_INFO, Unit, UnitStore, UnitType  # noqa: E402

TOL = 1e-9

# 원본 `UnitType` 문자열 → 우리 UnitType
UNIT_NAMES = {
    "City": UnitType.CITY,
    "Port": UnitType.PORT,
    "Factory": UnitType.FACTORY,
    "Defense Post": UnitType.DEFENSE_POST,
    "Missile Silo": UnitType.MISSILE_SILO,
    "SAM Launcher": UnitType.SAM_LAUNCHER,
    "Warship": UnitType.WARSHIP,
    "Atom Bomb": UnitType.ATOM_BOMB,
    "Hydrogen Bomb": UnitType.HYDROGEN_BOMB,
    "MIRV Warhead": UnitType.MIRV_WARHEAD,
    "MIRV": UnitType.MIRV,
}


class Report:
    def __init__(self) -> None:
        self.ok = 0
        self.bad: list[str] = []

    def check(self, name: str, want, got, tol: float = TOL) -> None:
        same = _close(want, got, tol)
        if same:
            self.ok += 1
        else:
            self.bad.append(f"  {name}\n      원본 {want!r}\n      우리 {got!r}")


def _close(a, b, tol: float) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    return a == b


def human(tiles: int, troops: float = 0.0, cities: list[int] | None = None) -> PlayerState:
    p = PlayerState(pid=0, name="H", kind="human", troops=troops or 1.0)
    p.troops = troops
    for lvl in cities or []:
        p.units.units.append(Unit(UnitType.CITY, 0, tile=0, level=lvl))
    return p


def bot(tiles: int, troops: float = 0.0) -> PlayerState:
    p = PlayerState(pid=1, name="B", kind="bot", troops=troops or 1.0)
    p.troops = troops
    return p


def verify(oracle: dict) -> Report:
    r = Report()

    # --- 병력 -------------------------------------------------------------
    tiles = [1, 100, 1_600, 37_575, 100_000, 2_000_000]
    r.check("maxTroops(human)", oracle["max_troops"],
            [human(n).max_troops(n) for n in tiles])
    r.check("maxTroops(bot)", oracle["max_troops_bot"],
            [bot(n).max_troops(n) for n in (1, 1_600, 100_000)])
    r.check("maxTroops(도시 Lv1+2+3)", oracle["max_troops_cities"],
            human(100, cities=[1, 2, 3]).max_troops(100))

    cases = [(1_000, 100), (50_000, 1_000), (500_000, 10_000)]
    r.check("troopIncreaseRate", oracle["troop_increase"],
            [human(t, troops=tr).troop_increase(t) for tr, t in cases])

    # --- 유닛 비용 --------------------------------------------------------
    for name, info in oracle["unit_costs"].items():
        ut = UNIT_NAMES[name]
        store = UnitStore()
        got = [store.cost(ut, extra=n) for n in (0, 1, 2, 4)]
        want = [info["cost0"], info["cost1"], info["cost2"], info["cost4"]]
        r.check(f"비용 {name}", want, got)
        r.check(f"건설 tick {name}", info["duration"],
                UNIT_INFO[ut].construction_ticks)
        r.check(f"체력 {name}", info["maxHealth"], UNIT_INFO[ut].max_health)

    # --- 핵 ---------------------------------------------------------------
    for name, mag in oracle["nuke_magnitudes"].items():
        r.check(f"핵 반경 {name}", mag, list(NUKE_MAGNITUDES[UNIT_NAMES[name]]))
    for name, sp in oracle["nuke_speed"].items():
        r.check(f"핵 속도 {name}", sp, NUKE_SPEED[UNIT_NAMES[name]])
    r.check("nukeDeathFactor(원폭)", oracle["nuke_death_factor"],
            [death_factor(UnitType.ATOM_BOMB, h, t, m)
             for h, t, m in ((100_000, 10_000, 200_000), (100_000, 100, 200_000),
                             (1_000, 1, 50_000))])
    r.check("nukeDeathFactor(MIRV 탄두)", oracle["nuke_death_factor_mirv"],
            [death_factor(UnitType.MIRV_WARHEAD, h, t, m)
             for h, t, m in ((20_000, 500, 1_000_000), (900_000, 500, 1_000_000),
                             (30_000, 500, 1_000_000))])
    r.check("samRange", oracle["sam_range"],
            [sam_range(l) for l in (1, 2, 3, 5, 10, 100)])

    # --- 무역·철도 --------------------------------------------------------
    r.check("tradeShipGold", oracle["trade_gold"],
            [trade_gold(d) for d in (50, 100, 300, 600, 1200)])
    r.check("tradeShipSpawnRate", oracle["trade_spawn_rate"],
            [trade_spawn_rate(rej, n) for rej, n in ((0, 0), (3, 0), (0, 400), (5, 100))])
    r.check("trainSpawnRate", oracle["train_spawn_rate"],
            [train_spawn_rate(f) for f in (0, 1, 10, 50)])
    r.check("trainGold", oracle["train_gold"],
            [train_gold(rel, n) for rel, n in
             (("self", 0), ("ally", 0), ("other", 0), ("team", 0),
              ("other", 9), ("other", 10), ("other", 12), ("self", 1000))])

    # --- 둠스데이 클락 ----------------------------------------------------
    times = [0, 300, 600, 601, 700, 768, 800, 900, 1200, 1800, 2100, 2400, 3000, 100000]
    for speed, want in oracle["doomsday_required_tiles"].items():
        r.check(f"둠스데이 바({speed})", want,
                [required_tiles(t, 10_000, speed) for t in times])
    r.check("둠스데이 바(팀전)", oracle["doomsday_team"],
            [required_tiles(t, 10_000, "normal", team_game=True)
             for t in (600, 700, 900, 1200, 2100)])

    # --- 골드·공격량 ------------------------------------------------------
    r.check("골드/tick 사람", oracle["gold_rate"]["human"], C.GOLD_PER_TICK_HUMAN)
    r.check("골드/tick 봇", oracle["gold_rate"]["bot"], C.GOLD_PER_TICK_BOT)
    aa = oracle["attack_amount"]
    r.check("attackAmount 사람", aa["human"], 50_000 * C.ATTACK_RATIO_HUMAN)
    r.check("attackAmount 봇", aa["bot"], 50_000 * C.ATTACK_RATIO_BOT)
    r.check("boatAttackAmount", aa["boat"], 50_000 * C.BOAT_ATTACK_RATIO)

    # --- 전투 공식 (이식의 핵심) ------------------------------------------
    terrain_maps = {
        "Plains": GameMap.from_rows(["."]),
        "Highland": GameMap.from_rows(["n"]),
        "Mountain": GameMap.from_rows(["A"]),
    }
    for i, c in enumerate(oracle["attack_logic_player"]):
        gm = terrain_maps[c["terrain"]]
        atk = human(c["aTiles"], troops=c["atk"] * 5)
        dfn = human(c["dTiles"], troops=c["dTroops"])
        dfn.pid = 1
        got = attack_logic(gm, 0, c["atk"], atk, dfn, c["dTiles"], c["aTiles"])
        tag = f'attackLogic[사람] {c["terrain"]} atk={c["atk"]}/dT={c["dTroops"]}/dTiles={c["dTiles"]}'
        r.check(f"{tag} 공격측손실", c["attackerTroopLoss"], got.attacker_loss)
        r.check(f"{tag} 수비측손실", c["defenderTroopLoss"], got.defender_loss)
        r.check(f"{tag} 예산소모", c["tilesPerTickUsed"], got.tiles_used)

    for c in oracle["attack_logic_neutral"]:
        gm = terrain_maps[c["terrain"]]
        atk = (bot(100, troops=c["atk"] * 5) if c["who"] == "bot"
               else human(100, troops=c["atk"] * 5))
        got = attack_logic(gm, 0, c["atk"], atk, None, 0, 100)
        tag = f'attackLogic[중립] {c["terrain"]} atk={c["atk"]} {c["who"]}'
        r.check(f"{tag} 공격측손실", c["attackerTroopLoss"], got.attacker_loss)
        r.check(f"{tag} 수비측손실", c["defenderTroopLoss"], got.defender_loss)
        r.check(f"{tag} 예산소모", c["tilesPerTickUsed"], got.tiles_used)

    for c in oracle["attack_tiles_per_tick"]:
        dfn = human(1, troops=c["dTroops"])
        tag = f'attackTilesPerTick atk={c["atk"]}/dT={c["dTroops"]}/border={c["border"]}'
        r.check(f"{tag} vs사람", c["vsPlayer"],
                tiles_per_tick(c["atk"], dfn, c["border"]))
        r.check(f"{tag} vs중립", c["vsNeutral"],
                tiles_per_tick(c["atk"], None, c["border"]))

    # --- 스칼라 상수 ------------------------------------------------------
    s = oracle["scalars"]
    pairs = [
        ("turnIntervalMs", C.TICK_MS), ("boatMaxNumber", C.BOAT_MAX_NUMBER),
        ("structureMinDist", C.STRUCTURE_MIN_DIST),
        ("defensePostRange", C.DEFENSE_POST_RANGE),
        ("defensePostDefenseBonus", C.DEFENSE_POST_DEFENSE_BONUS),
        ("defensePostSpeedBonus", C.DEFENSE_POST_SPEED_BONUS),
        ("cityTroopIncrease", C.CITY_TROOP_INCREASE),
        ("allianceDuration", C.ALLIANCE_DURATION_TICKS),
        ("traitorDefenseDebuff", C.TRAITOR_DEFENSE_DEBUFF),
        ("traitorSpeedDebuff", C.TRAITOR_SPEED_DEBUFF),
        ("traitorDuration", C.TRAITOR_DURATION_TICKS),
        ("warshipPatrolRange", C.WARSHIP_PATROL_RANGE),
        ("warshipTargettingRange", C.WARSHIP_TARGETTING_RANGE),
        ("warshipShellAttackRate", C.WARSHIP_SHELL_ATTACK_RATE),
        ("warshipPassiveHealing", C.WARSHIP_PASSIVE_HEALING),
        ("warshipPassiveHealingRange", C.WARSHIP_PASSIVE_HEALING_RANGE),
        ("warshipVeterancyShellDamageBonus", C.WARSHIP_VETERANCY_SHELL_BONUS),
        ("shellLifetime", C.SHELL_LIFETIME),
        ("tradeShipShortRangeDebuff", C.TRADE_SHORT_RANGE_DEBUFF),
        ("trainStationMinRange", C.TRAIN_STATION_MIN_RANGE),
        ("trainStationMaxRange", C.TRAIN_STATION_MAX_RANGE),
        ("defaultSamRange", C.DEFAULT_SAM_RANGE), ("maxSamRange", C.MAX_SAM_RANGE),
        ("donateCooldown", C.DONATE_COOLDOWN_TICKS),
        ("waterNukes", C.WATER_NUKES),
        ("startManpowerHuman", C.START_TROOPS_HUMAN),
        ("startManpowerBot", C.START_TROOPS_BOT),
    ]
    for key, ours in pairs:
        r.check(key, s[key], ours)

    # 스폰 최소 거리는 `core/spawn.py` 에 있다
    from domynion.core.spawn import MIN_DISTANCE_BETWEEN_PLAYERS
    r.check("minDistanceBetweenPlayers", s["minDistanceBetweenPlayers"],
            MIN_DISTANCE_BETWEEN_PLAYERS)
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="원본 실행값과 우리 구현을 대조한다")
    ap.add_argument("oracle", type=Path, help="tools/oracle.mts 가 낸 JSON")
    args = ap.parse_args(argv)

    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))
    r = verify(oracle)

    total = r.ok + len(r.bad)
    if r.bad:
        print(f"❌ {len(r.bad)}/{total} 불일치\n")
        print("\n".join(r.bad))
        return 1
    print(f"✅ {r.ok}/{total} 항목이 원본 실행값과 일치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
