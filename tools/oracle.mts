/**
 * 원본 함수를 **실제로 실행해** 값을 JSON 으로 뽑는다.
 *
 * 이식이 맞는지 확인하는 가장 강한 방법은 "코드를 눈으로 대조"가 아니라
 * **같은 입력에 같은 출력이 나오는가**다. Node 22+ 는 TypeScript 를 그대로
 * 실행하므로(type stripping) 원본 소스를 고치지 않고 부를 수 있다.
 *
 *     node tools/oracle.mts <원본_리포_경로> > oracle.json
 *
 * `Config.ts` 의 메서드 상당수는 `this._gameConfig` 만 보므로 가짜 설정으로
 * 인스턴스를 만들어 부른다. Game/Player 가 필요한 것(attackLogic 등)은
 * **최소한의 가짜 객체**를 만들어 넘긴다 — 그 가짜가 원본의 어떤 값을 쓰는지는
 * 소스에 그대로 드러나 있다.
 */
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const root = process.argv[2];
if (!root) {
  console.error("사용법: node tools/oracle.mts <원본_리포_경로>");
  process.exit(2);
}
const load = (p: string) => import(pathToFileURL(join(root, p)).href);

const out: Record<string, unknown> = {};

// --- 둠스데이 클락 (순수 함수) ---------------------------------------------
{
  const m = await load("src/core/game/DoomsdayClock.ts");
  const land = 10_000;
  const bars: Record<string, number[]> = {};
  for (const speed of m.DOOMSDAY_CLOCK_SPEEDS) {
    bars[speed] = [0, 300, 600, 601, 700, 768, 800, 900, 1200, 1800, 2100,
                   2400, 3000, 100000].map(
      // ⚠ 시그니처는 `(profile, land, elapsed)` 다. 처음에 (profile, elapsed, land)
      // 로 넘겼다가 "원본이 시간 × 0.35 를 낸다"는 말이 안 되는 값을 보고 알았다.
      (t) => m.doomsdayClockRequiredTiles({ speed }, land, t));
  }
  out.doomsday_required_tiles = bars;
  out.doomsday_team = [600, 700, 900, 1200, 2100].map(
    (t) => m.doomsdayClockRequiredTiles({ speed: "normal", teamGame: true }, land, t));
}

// --- Config 순수 계산 -------------------------------------------------------
{
  const cfg = await load("src/core/configuration/Config.ts");
  const { UnitType, PlayerType, Difficulty } = await load("src/core/game/Game.ts");

  // 최소한의 가짜 설정. 원본 DefaultConfig 는 (serverConfig, gameConfig, ...) 를
  // 받는데, 아래 메서드들은 gameConfig 의 몇 필드만 본다.
  const gameConfig = {
    difficulty: Difficulty.Medium,
    gameMode: "Free For All",
    gameType: "Singleplayer",
    disabledUnits: [],
    infiniteGold: false,
    infiniteTroops: false,
    instantBuild: false,
    bots: 0,
    nations: "enabled",
  };
  // `Config(gameConfig, userSettings, isReplay, listed)` — 이름이 `DefaultConfig`
  // 가 아니라 `Config` 다. export 목록을 직접 찍어 확인했다.
  const C = new cfg.Config(gameConfig as never, null, false, false);

  const player = (tiles: number, troops: number, type = PlayerType.Human,
                  cities: number[] = []) => ({
    numTilesOwned: () => tiles,
    troops: () => troops,
    type: () => type,
    isPlayer: () => true,
    isAlive: () => true,
    isTraitor: () => false,
    isDisconnected: () => false,
    isOnSameTeam: () => false,
    isLobbyCreator: () => false,
    units: (t?: unknown) =>
      t === UnitType.City
        ? cities.map((lvl) => ({ level: () => lvl, isUnderConstruction: () => false }))
        : [],
    unitsOwned: () => 0,
    unitsConstructed: () => 0,
    id: () => "p",
  });

  out.max_troops = [1, 100, 1_600, 37_575, 100_000, 2_000_000].map(
    (n) => C.maxTroops(player(n, 0) as never));
  out.max_troops_bot = [1, 1_600, 100_000].map(
    (n) => C.maxTroops(player(n, 0, PlayerType.Bot) as never));
  out.max_troops_cities = C.maxTroops(player(100, 0, PlayerType.Human, [1, 2, 3]) as never);

  out.troop_increase = [[1_000, 100], [50_000, 1_000], [500_000, 10_000]].map(
    ([troops, tiles]) => C.troopIncreaseRate(player(tiles, troops) as never));

  out.unit_costs = {};
  for (const t of [UnitType.City, UnitType.Port, UnitType.Factory,
                   UnitType.DefensePost, UnitType.MissileSilo, UnitType.SAMLauncher,
                   UnitType.Warship, UnitType.AtomBomb, UnitType.HydrogenBomb]) {
    const info = C.unitInfo(t);
    (out.unit_costs as Record<string, unknown>)[t] = {
      cost0: Number(info.cost({} as never, player(0, 0) as never, 0)),
      cost1: Number(info.cost({} as never, player(0, 0) as never, 1)),
      cost2: Number(info.cost({} as never, player(0, 0) as never, 2)),
      cost4: Number(info.cost({} as never, player(0, 0) as never, 4)),
      duration: info.constructionDuration ?? 0,
      maxHealth: info.maxHealth ?? null,
    };
  }

  // --- 업그레이드 비용 곡선 --------------------------------------------------
  //
  // ⚠ 위 `unit_costs` 는 `extra` 만 흔들고 `unitsOwned`/`unitsConstructed` 는
  // **0 으로 고정된 가짜**를 쓴다. 그래서 그 축의 버그를 하나도 못 잡았다 —
  // 실제로 우리 `unitsOwned` 가 레벨 합이 아니라 개수였는데 여기서 안 드러났다.
  // 오라클의 가짜가 상수를 돌려주는 축은 **검증되지 않는 축**이다.
  //
  // 여기서는 원본 `upgradeUnit()` 이 하는 일을 그대로 흉내 낸다:
  //   cost = unitInfo(t).cost(mg, this)   ← 지금 상태로 값을 매기고
  //   unit.increaseLevel()                ← unitsOwned 가 레벨 합이라 +1
  //   recordUnitConstructed(t)            ← unitsConstructed +1
  out.upgrade_costs = {};
  for (const t of [UnitType.City, UnitType.Port, UnitType.MissileSilo,
                   UnitType.SAMLauncher]) {
    // ⚠ **타입을 봐야 한다.** 처음엔 인자를 무시하고 늘 같은 수를 돌려줬는데,
    // 항구는 공장과 비용을 공유해서(`costWrapper(fn, Port, Factory)`) 원본이
    // 두 종류를 각각 세는 바람에 **항구 값이 두 배로 나왔다.** 그 상태로는
    // 하네스가 틀린 값을 내고 우리 코드를 불일치로 몰아세운다.
    let level = 1, constructed = 1;
    const up: any = {
      type: () => PlayerType.Human,
      unitsOwned: (q: unknown) => (q === t ? level : 0),
      unitsConstructed: (q: unknown) => (q === t ? constructed : 0),
    };
    const info = C.unitInfo(t);
    const seq: number[] = [];
    for (let i = 0; i < 5; i++) {
      seq.push(Number(info.cost({} as never, up as never)));
      level++; constructed++;
    }
    (out.upgrade_costs as Record<string, unknown>)[t] = seq;
  }

  out.sam_range = [1, 2, 3, 5, 10, 100].map((l) => C.samRange(l));
  out.trade_gold = [50, 100, 300, 600, 1200].map(
    (d) => Number(C.tradeShipGold(d, player(1, 1) as never)));
  out.trade_spawn_rate = [[0, 0], [3, 0], [0, 400], [5, 100]].map(
    ([rej, n]) => C.tradeShipSpawnRate(rej, n));
  out.train_spawn_rate = [0, 1, 10, 50].map((f) => C.trainSpawnRate(f));
  out.train_gold = [["self", 0], ["ally", 0], ["other", 0], ["team", 0],
                    ["other", 9], ["other", 10], ["other", 12], ["self", 1000]].map(
    ([rel, n]) => Number(C.trainGold(rel as never, n as number, player(1, 1) as never)));

  out.nuke_magnitudes = {};
  for (const t of [UnitType.AtomBomb, UnitType.HydrogenBomb, UnitType.MIRVWarhead]) {
    const m = C.nukeMagnitudes(t);
    (out.nuke_magnitudes as Record<string, unknown>)[t] = [m.inner, m.outer];
  }
  out.nuke_speed = {};
  for (const t of [UnitType.AtomBomb, UnitType.HydrogenBomb, UnitType.MIRV,
                   UnitType.MIRVWarhead]) {
    (out.nuke_speed as Record<string, unknown>)[t] = C.nukeSpeed(t);
  }
  out.nuke_death_factor = [
    [100_000, 10_000, 200_000], [100_000, 100, 200_000], [1_000, 1, 50_000],
  ].map(([h, t, m]) => C.nukeDeathFactor(UnitType.AtomBomb, h, t, m));
  out.nuke_death_factor_mirv = [
    [20_000, 500, 1_000_000], [900_000, 500, 1_000_000], [30_000, 500, 1_000_000],
  ].map(([h, t, m]) => C.nukeDeathFactor(UnitType.MIRVWarhead, h, t, m));

  out.gold_rate = {
    human: Number(C.goldAdditionRate(player(1, 1) as never)),
    bot: Number(C.goldAdditionRate(player(1, 1, PlayerType.Bot) as never)),
  };

  out.scalars = {
    turnIntervalMs: 100,
    boatMaxNumber: C.boatMaxNumber(),
    structureMinDist: C.structureMinDist(),
    defensePostRange: C.defensePostRange(),
    defensePostDefenseBonus: C.defensePostDefenseBonus(),
    defensePostSpeedBonus: C.defensePostSpeedBonus(),
    cityTroopIncrease: C.cityTroopIncrease(),
    allianceDuration: C.allianceDuration(),
    traitorDefenseDebuff: C.traitorDefenseDebuff(),
    traitorSpeedDebuff: C.traitorSpeedDebuff(),
    traitorDuration: C.traitorDuration(),
    minDistanceBetweenPlayers: C.minDistanceBetweenPlayers(),
    warshipPatrolRange: C.warshipPatrolRange(),
    warshipTargettingRange: C.warshipTargettingRange(),
    warshipShellAttackRate: C.warshipShellAttackRate(),
    warshipPassiveHealing: C.warshipPassiveHealing(),
    warshipPassiveHealingRange: C.warshipPassiveHealingRange(),
    warshipVeterancyShellDamageBonus: C.warshipVeterancyShellDamageBonus(),
    shellLifetime: C.shellLifetime(),
    tradeShipShortRangeDebuff: C.tradeShipShortRangeDebuff(),
    trainStationMinRange: C.trainStationMinRange(),
    trainStationMaxRange: C.trainStationMaxRange(),
    defaultSamRange: C.defaultSamRange(),
    maxSamRange: C.maxSamRange(),
    donateCooldown: C.donateCooldown(),
    waterNukes: C.waterNukes(),
    startManpowerHuman: C.startManpower({ playerType: PlayerType.Human } as never),
    startManpowerBot: C.startManpower({ playerType: PlayerType.Bot } as never),
    startManpowerNation: C.startManpower({ playerType: PlayerType.Nation } as never),
  };

  // --- 전투 공식 — 이식의 핵심 --------------------------------------------
  //
  // `attackLogic` 은 Game 과 Player 를 받는다. 가짜로 만들되 **원본이 실제로 무엇을
  // 읽는지**는 소스에 그대로 드러나 있다: terrainType · nearbyUnits · hasFallout ·
  // numTilesWithFallout · numLandTiles, 그리고 양쪽의 type/troops/numTilesOwned/
  // isTraitor/isDisconnected/isOnSameTeam.
  const terraNullius = { isPlayer: () => false, id: () => "TN" };
  const fakeGame = (terrain: unknown) => ({
    terrainType: () => terrain,
    nearbyUnits: () => [],
    hasFallout: () => false,
    numTilesWithFallout: () => 0,
    numLandTiles: () => 100_000,
    config: () => C,
  });

  const TT = (await load("src/core/game/Game.ts")).TerrainType;
  const attackCases: unknown[] = [];
  for (const [tname, terrain] of [["Plains", TT.Plains], ["Highland", TT.Highland],
                                  ["Mountain", TT.Mountain]] as const) {
    for (const [atk, dTroops, dTiles, aTiles] of [
      [4_000, 40_000, 500, 800], [20_000, 40_000, 500, 800],
      [1_000, 1_000_000, 200_000, 300_000], [50_000, 5_000, 50, 100],
    ] as const) {
      const g = fakeGame(terrain);
      const a = player(aTiles, atk * 5);
      const d = player(dTiles, dTroops);
      const res = C.attackLogic(g as never, atk, a as never, d as never, 0 as never);
      attackCases.push({
        terrain: tname, atk, dTroops, dTiles, aTiles,
        attackerTroopLoss: res.attackerTroopLoss,
        defenderTroopLoss: res.defenderTroopLoss,
        tilesPerTickUsed: res.tilesPerTickUsed,
      });
    }
  }
  out.attack_logic_player = attackCases;

  const neutralCases: unknown[] = [];
  for (const [tname, terrain] of [["Plains", TT.Plains], ["Highland", TT.Highland],
                                  ["Mountain", TT.Mountain]] as const) {
    for (const atk of [1_000, 25_000, 100_000]) {
      for (const [who, type] of [["human", PlayerType.Human],
                                 ["bot", PlayerType.Bot]] as const) {
        const g = fakeGame(terrain);
        const a = player(100, atk * 5, type);
        const res = C.attackLogic(g as never, atk, a as never,
                                  terraNullius as never, 0 as never);
        neutralCases.push({
          terrain: tname, atk, who,
          attackerTroopLoss: res.attackerTroopLoss,
          defenderTroopLoss: res.defenderTroopLoss,
          tilesPerTickUsed: res.tilesPerTickUsed,
        });
      }
    }
  }
  out.attack_logic_neutral = neutralCases;

  out.attack_tiles_per_tick = [];
  for (const [atk, dTroops, border] of [
    [5_000, 20_000, 100], [5_000, 20_000, 200], [10_000_000, 1, 10], [1, 1e7, 10],
  ] as const) {
    (out.attack_tiles_per_tick as unknown[]).push({
      atk, dTroops, border,
      vsPlayer: C.attackTilesPerTick(atk, player(1, atk) as never,
                                     player(1, dTroops) as never, border),
      vsNeutral: C.attackTilesPerTick(atk, player(1, atk) as never,
                                      terraNullius as never, border),
    });
  }

  out.attack_amount = {
    human: C.attackAmount(player(1, 50_000) as never, player(1, 1) as never),
    bot: C.attackAmount(player(1, 50_000, PlayerType.Bot) as never,
                        player(1, 1) as never),
    boat: C.boatAttackAmount(player(1, 50_000) as never, player(1, 1) as never),
  };
}

console.log(JSON.stringify(out, null, 2));
