"""이모지 — AI 가 사람에게 말을 거는 유일한 통로.

원본에서 이건 장식이 아니라 규칙이다. 🖕 하나가 상대 관계를 −100 움직인다 —
사람이 AI 의 눈을 바꾸는 방법 중 유일하게 공짜다(공격은 병력을, 기부는 골드를 쓴다).

출처: `NationEmojiBehavior.ts` · `PlayerImpl.canSendEmoji` · `Util.ts :: emojiTable`
"""

from __future__ import annotations

import random

import pytest

from domynion.ai.nation import NationBot
from domynion.core import constants as C
from domynion.core import emoji
from domynion.core.buildings import DefensePostIndex
from domynion.core.engine import GameState
from domynion.core.events import Category, EventKind
from domynion.core.gamemap import GameMap
from domynion.core.nukes import Fallout
from domynion.core.relations import Relation
from domynion.core.state import PlayerState


def state(kinds: dict[int, str] | None = None,
          difficulty: str = "medium") -> GameState:
    kinds = kinds or {0: "human", 1: "nation"}
    gm = GameMap.from_rows(["." * 40] * 4)
    players = {}
    for pid, kind in kinds.items():
        for x in range(pid * 5, pid * 5 + 5):
            gm.owner[gm.ref(x, 0)] = pid
        p = PlayerState(pid=pid, name=f"P{pid}", start=gm.ref(pid * 5, 0))
        p.kind = kind
        p.is_bot = kind == "bot"
        p.troops = 100_000.0
        p.gold = 200_000_000
        players[pid] = p
    st = GameState(gmap=gm, players=players, rng=random.Random(0),
                   difficulty=difficulty)
    st._counts = {pid: 5 for pid in players}
    st._posts = DefensePostIndex(gm.size)
    st.fallout = Fallout(gm.size)
    st.tick_count = C.SPAWN_IMMUNITY_TICKS
    return st


def chats(st: GameState) -> list[str]:
    return [e.text for e in st.log.items if e.kind is EventKind.CHAT]


# --- 표 ---------------------------------------------------------------------

def test_table_is_twelve_by_five():
    """원본 `emojiTable` 그대로. 줄 수가 바뀌면 UI 격자도 어긋난다."""
    assert len(emoji.EMOJI_TABLE) == 12
    assert all(len(row) == 5 for row in emoji.EMOJI_TABLE)


def test_the_emojis_that_matter_are_in_the_table():
    flat = [ch for row in emoji.EMOJI_TABLE for ch in row]
    assert emoji.INSULT in flat and emoji.CLOWN in flat
    for ch in emoji.PEACEFUL:
        assert ch in flat, f"{ch} 를 고를 수 없으면 규칙이 죽은 것이다"


# --- 관계 변화 --------------------------------------------------------------

def test_insult_is_the_free_way_to_wreck_a_relation():
    st = state()
    assert st.send_emoji(0, 1, emoji.INSULT)
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_EMOJI_INSULT)
    assert st.relation_of(1, 0) is Relation.HOSTILE


def test_clown_stings_a_little():
    st = state()
    st.send_emoji(0, 1, emoji.CLOWN)
    assert st.players[1].relations.value(0) == pytest.approx(C.REL_EMOJI_CLOWN)


def test_kind_emojis_only_work_on_easy():
    """막지 않았으면: 쿨다운마다 ❤️ 를 눌러 관계를 공짜로 산다."""
    easy = state(difficulty="easy")
    easy.send_emoji(0, 1, "❤️")
    assert easy.players[1].relations.value(0) == pytest.approx(
        C.REL_EMOJI_PEACEFUL)

    med = state(difficulty="medium")
    med.send_emoji(0, 1, "❤️")
    assert med.players[1].relations.value(0) == pytest.approx(0.0)


def test_most_emojis_do_nothing_to_relations():
    st = state()
    st.send_emoji(0, 1, "🐔")
    assert st.players[1].relations.value(0) == pytest.approx(0.0)


def test_emojis_between_humans_are_just_words():
    """사람끼리는 관계가 안 움직인다 — 원본도 받는 쪽이 Nation 일 때만 반응한다."""
    st = state({0: "human", 1: "human"})
    assert st.send_emoji(0, 1, emoji.INSULT)
    assert st.players[1].relations.value(0) == pytest.approx(0.0)


# --- 답장 -------------------------------------------------------------------

def test_an_insult_gets_answered():
    st = state()
    st.send_emoji(0, 1, emoji.INSULT)
    got = chats(st)
    assert got[0] == emoji.INSULT
    assert len(got) == 2 and got[1] in emoji.GOT_INSULTED


def test_the_answer_to_kindness_depends_on_how_it_sees_me():
    """사이가 나쁘면 사랑이 아니라 물음표가 돌아온다."""
    warm = emoji.reply_to("❤️", random.Random(0), Relation.FRIENDLY)
    cold = emoji.reply_to("❤️", random.Random(0), Relation.HOSTILE)
    assert warm in emoji.LOVE
    assert cold in emoji.CONFUSED


def test_a_plain_emoji_gets_no_answer():
    st = state()
    st.send_emoji(0, 1, "🐔")
    assert len(chats(st)) == 1


# --- 쿨다운 -----------------------------------------------------------------

def test_five_second_cooldown_between_the_same_pair():
    st = state()
    assert st.send_emoji(0, 1, "👋")
    assert not st.send_emoji(0, 1, "👋"), "도배를 막아야 한다"
    st.tick_count += C.EMOJI_COOLDOWN_TICKS
    assert st.send_emoji(0, 1, "👋")


def test_the_cooldown_is_per_pair_not_global():
    st = state({0: "human", 1: "nation", 2: "nation"})
    assert st.send_emoji(0, 1, "👋")
    assert st.send_emoji(0, 2, "👋"), "다른 상대는 따로 센다"


def test_cooldown_matches_the_original():
    assert C.EMOJI_COOLDOWN_TICKS * C.TICK_DT == 5.0
    assert C.EMOJI_AI_INTERVAL_TICKS * C.TICK_DT == 30.0


# --- AI 가 먼저 거는 말 -----------------------------------------------------

def test_ai_only_speaks_to_humans():
    """**AI 끼리는 주고받지 않는다.** 그래서 화면의 이모지는 전부 나에게 온 말이다.

    막지 않았으면: 로그가 남의 대화로 덮여 내 소식이 묻힌다."""
    st = state({0: "nation", 1: "nation"})
    assert st.ai_emoji(0, 1, emoji.LOVE) is False
    assert chats(st) == []


def test_bots_never_speak():
    st = state({0: "bot", 1: "human"})
    assert st.ai_emoji(0, 1, emoji.LOVE) is False


def test_ai_waits_thirty_seconds_before_speaking_again():
    """막지 않았으면: AI 가 판단할 때마다 말을 걸어 화면이 이모지로 덮인다."""
    st = state({0: "nation", 1: "human"})
    assert st.ai_emoji(0, 1, emoji.LOVE)
    st.tick_count += C.EMOJI_COOLDOWN_TICKS     # 5초 쿨다운은 지났다
    assert not st.ai_emoji(0, 1, emoji.LOVE), "30초 제한이 따로 있다"
    st.tick_count += C.EMOJI_AI_INTERVAL_TICKS
    assert st.ai_emoji(0, 1, emoji.LOVE)


def test_the_thirty_second_clock_starts_when_the_check_passes():
    """확인과 기록을 나눠 두면 호출부가 기록을 잊어 제한이 조용히 사라진다."""
    st = state({0: "nation", 1: "human"})
    assert st.emojis.ai_may_speak(0, 1, st.tick_count)
    assert not st.emojis.ai_may_speak(0, 1, st.tick_count)


# --- 엔진 안에서 실제로 나오는가 --------------------------------------------

def test_ai_says_something_when_it_nukes_me():
    st = state({0: "nation", 1: "human"})
    from domynion.core.units import Unit, UnitType
    st.players[0].units.units.append(
        Unit(UnitType.MISSILE_SILO, 0, tile=st.gmap.ref(0, 0)))
    assert st.launch_nuke(0, UnitType.ATOM_BOMB, st.gmap.ref(6, 0)) is not None
    assert chats(st) and chats(st)[0] in emoji.NUKE


def test_ai_answers_a_donation_and_says_when_it_was_too_small():
    big = state({0: "human", 1: "nation"})
    big.diplomacy.form(0, 1, big.tick_count)      # 기부는 친한 사이만 (§5.63)
    big.donate_gold(0, 1, 200_000)
    assert chats(big)[0] in emoji.LOVE + emoji.DONATION_OK

    small = state({0: "human", 1: "nation"})
    small.diplomacy.form(0, 1, small.tick_count)
    small.donate_gold(0, 1, 1)
    assert chats(small)[0] in emoji.DONATION_TOO_SMALL


def test_ai_shakes_hands_when_it_accepts():
    """수락하면 악수를 보낸다 — 다만 **1/3 확률**이다.

    ⚠ §5.53 에서 바뀐 전제다. 옛 축소판은 수락할 때마다 보냈는데 원본은
    `if (this.random.chance(3)) sendEmoji(HANDSHAKE)` 다. seed 를 여러 개 돌려
    "적어도 가끔은 보낸다"로 재고, 수락 자체는 매번 확인한다."""
    shook = accepted = 0
    for seed in range(12):
        st = state({0: "human", 1: "nation"})
        bot = NationBot(pid=1, rng=random.Random(seed), difficulty="medium")
        st.players[1].relations.update(0, 80)   # 우호 → 대체로 수락
        if bot._accepts_alliance(st, 0):
            accepted += 1
            said = chats(st)
            if said:
                assert said[0] in emoji.HANDSHAKE, said
                shook += 1
    # ⚠ 우호여도 **매번 수락하지는 않는다** — medium 은 5% 로 혼란에 빠진다
    # (§5.53 의 첫 관문). 실측: seed 9 만 거절한다.
    assert accepted >= 10, f"우호인데 {accepted}/12 만 수락 — 관계를 안 본다"
    assert 0 < shook < accepted, f"악수가 {shook}/{accepted} — 확률이 안 걸렸다"


# --- 배신자 규칙(이모지와 같이 들어온 것) -----------------------------------

def test_ai_almost_always_rejects_a_traitor():
    """관계만 보면 방금 남을 배신한 자가 중립이라는 이유로 받아들여진다.

    막지 않았으면: 배신에 비용이 없어져 동맹이 의미를 잃는다."""
    accepted = 0
    for seed in range(100):
        st = state({0: "human", 1: "nation"})
        st.diplomacy.form(0, 2, tick=0)
        st.diplomacy.break_alliance(0, 2, tick=st.tick_count)
        assert st.is_traitor(0)
        bot = NationBot(pid=1, rng=random.Random(seed), difficulty="medium")
        st.players[1].relations.update(0, 80)   # 우호인데도
        accepted += bot._accepts_alliance(st, 0)
    assert accepted < 25, f"배신자를 {accepted}/100 이나 받아 줬다"


# --- 로그 -------------------------------------------------------------------

def test_chat_has_its_own_category():
    """공격·핵 사이에 말이 섞이면 급한 것이 묻힌다."""
    from domynion.core.events import CATEGORY
    assert CATEGORY[EventKind.CHAT] is Category.CHAT


def test_the_message_reaches_the_person_it_was_sent_to():
    st = state()
    st.send_emoji(0, 1, "👋")
    got = st.log.recent(who=1, count=5)
    assert got and got[0].text == "👋"


# --- 지도에 뜨는 말 (§5.96) ----------------------------------------------------

def test_a_sent_emoji_shows_on_the_map_for_five_seconds_then_goes():
    """⚠ **소식창은 흘러가고 지도는 남는다.** 우리는 `CHAT` 이벤트만 냈다 —
    AI 가 던진 🖕 하나가 관계를 −100 움직이는데 화면에서는 아무 일도 안 일어났다.

    막지 않았으면: 규칙은 다 도는데 사람은 누가 무슨 말을 했는지 모른다."""
    st = state()
    assert st.send_emoji(0, 1, "👋")
    assert st.emojis.visible_to(1, st.tick_count) == {0: "👋"}

    # ⚠ **경계는 `tick - 보낸시각 < 수명`** 이다(원본 `outgoingEmojis` 의 `<`).
    # 딱 `수명` 째에는 이미 사라진다 — 처음에 `<=` 로 알고 테스트를 틀리게 썼다.
    t0 = st.tick_count
    assert st.emojis.visible_to(1, t0 + C.EMOJI_MESSAGE_DURATION_TICKS - 1) == {0: "👋"}
    assert st.emojis.visible_to(1, t0 + C.EMOJI_MESSAGE_DURATION_TICKS) == {},         "수명이 지났는데 남아 있다"


def test_i_do_not_see_what_other_people_say_to_each_other():
    """⚠ 원본이 `recipientID === AllPlayers || === myPlayer` 로 거른다.

    막지 않았으면: 400나라가 주고받는 말이 전부 떠서 지도가 이모지로 덮인다."""
    st = state({0: "human", 1: "nation", 2: "nation"})
    assert st.send_emoji(1, 2, "👋")
    assert st.emojis.visible_to(0, st.tick_count) == {}, "남의 말이 보인다"
    assert st.emojis.visible_to(2, st.tick_count) == {1: "👋"}


def test_a_broadcast_is_visible_to_everyone_and_stored_once():
    """전체에 대고 한 말(`sendEmoji(AllPlayers, ...)`)은 누구에게나 보인다.

    ⚠ **한 번만 담는다.** 소식은 사람마다 하나씩 내지만 지도 표시는 *한 나라가
    한 말*이라, 받는 사람 수만큼 쌓으면 중복이 된다.

    막지 않았으면: 사람이 둘 이상인 판에서 같은 말이 여러 번 담긴다."""
    st = state({0: "human", 1: "nation", 2: "human"})
    before = len(st.emojis.outgoing)
    assert st.ai_broadcast(1, ("😰",))
    assert len(st.emojis.outgoing) - before == 1, "받는 사람 수만큼 쌓였다"
    assert st.emojis.visible_to(0, st.tick_count) == {1: "😰"}
    assert st.emojis.visible_to(2, st.tick_count) == {1: "😰"}


def test_a_reply_is_credited_to_whoever_actually_said_it():
    """⚠ 답장은 **받는 쪽이 보낸 말**이라 보낸 이가 뒤집힌다.

    막지 않았으면: 내가 보낸 자리에 상대의 답이 붙어, 내가 두 번 말한 것처럼
    보인다 — 그리고 **정작 상대의 말은 화면에 안 뜬다.**

    ⚠ 모욕(`INSULT`)을 쓴다. 그 갈래만 관계와 무관하게 **항상** 답이 나온다
    (`reply_to`) — 확률에 기대면 답이 안 나온 판에서 아무것도 안 재게 된다."""
    st = state()
    assert st.send_emoji(0, 1, emoji.INSULT)

    said = st.emojis.visible_to(0, st.tick_count)
    assert set(said) == {1}, f"답을 보낸 이가 틀렸다: {said}"
    assert said[1] in emoji.GOT_INSULTED

    # 내가 보낸 말은 **상대 화면**에 있다. 두 방향이 섞이지 않아야 한다.
    assert st.emojis.visible_to(1, st.tick_count)[0] == emoji.INSULT


def test_nothing_is_visible_to_a_spectator_except_broadcasts():
    """관전·헤드리스 경로. 원본도 `myPlayer` 가 없으면 개인 메시지를 못 고른다."""
    st = state({0: "human", 1: "nation"})
    assert st.send_emoji(1, 0, "👋")
    assert st.emojis.visible_to(None, st.tick_count) == {}


def test_the_engine_throws_away_expired_messages():
    """⚠ **배선이다.** 조회는 읽기만 하므로, 엔진이 안 버리면 목록이 판 내내
    쌓인다 — 400나라가 30초에 한 번씩 말하면 한 시간에 수만 개다.

    막지 않았으면: 화면은 멀쩡한데 메모리만 는다 — 테스트가 잡을 신호가 없다."""
    st = state()
    st.send_emoji(0, 1, "👋")
    assert st.emojis.outgoing
    st.tick_count += C.EMOJI_MESSAGE_DURATION_TICKS + 1
    st.tick()
    assert st.emojis.outgoing == [], "수명이 지난 말을 아무도 안 버린다"


def test_only_the_latest_thing_someone_said_is_shown():
    """⚠ 원본도 `createdAt` 내림차순으로 정렬한 뒤 `find` 로 **첫 것만** 쓴다.
    한 나라가 5초 안에 두 번 말하면 뒤엣말이 이긴다.

    막지 않았으면: 가장 오래된 것을 골라도 이모지는 뜨므로 눈에 안 띈다 —
    실제로 그 변이가 살아남았다. 재료로 **같은 나라의 말 둘**이 필요하다."""
    st = state()
    st.emojis.outgoing.append((1, 0, "👋", st.tick_count))
    st.emojis.outgoing.append((1, 0, "🖕", st.tick_count + 1))
    assert st.emojis.visible_to(0, st.tick_count + 1) == {1: "🖕"}

    # 목록에 담긴 순서가 아니라 **시각**으로 고른다.
    st.emojis.outgoing.reverse()
    assert st.emojis.visible_to(0, st.tick_count + 1) == {1: "🖕"}
