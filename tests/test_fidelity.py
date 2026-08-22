"""원본 대조 — **같은 입력에 같은 출력이 나오는가.**

`tools/oracle.mts` 가 원본 TypeScript 를 실제로 실행해 뽑은 값이 `tests/oracle.json`
에 들어 있다. 이 테스트는 같은 입력을 우리 구현에 넣어 대조한다.

이게 이식이 맞다는 **유일한 근거**다. "코드를 눈으로 봤다"나 "테스트가 통과한다"는
근거가 아니다 — 실제로 둠스데이 클락 인자 순서를 뒤집어 놓고도 우리 테스트는 전부
통과하고 있었다(그건 하네스 쪽 실수였지만, 대조가 없었으면 몰랐을 종류다).

⚠ **하네스의 가짜 객체가 상수를 돌려주는 축은 검증되지 않는다.** `unit_costs` 는
`extra` 축만 흔들고 `unitsOwned`/`unitsConstructed` 는 0 으로 굳어 있었는데, 바로
거기가 틀려 있었다(`unitsOwned` 가 개수가 아니라 레벨 합이다). `upgrade_costs` 를
따로 둔 이유가 그것이다 — 대조 항목이 있다고 그 축이 재어진 건 아니다.

기준값을 다시 뽑으려면(2026-08-22 실측 절차):

    git clone --depth 1 --filter=blob:none --no-checkout         https://github.com/openfrontio/OpenFrontIO.git of
    cd of
    git sparse-checkout set --no-cone "/*" "!/*/" "src/**" "resources/*.json"
    git checkout && npm install
    ./node_modules/.bin/tsx <이리포>/tools/oracle.mts . > <이리포>/tests/oracle.json

`node --experimental-strip-types` 로는 안 된다(원본이 `enum` 을 쓴다). sparse 패턴에
`"/*" "!/*/"` 를 빼면 `package.json` 이 없어 `npm install` 이 ENOENT 로 죽는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools_verify import verify        # noqa: F401  (conftest 가 경로를 넣어 준다)

ORACLE = Path(__file__).with_name("oracle.json")


@pytest.mark.skipif(not ORACLE.is_file(), reason="기준값 파일이 없다")
def test_every_ported_value_matches_the_original():
    report = verify(json.loads(ORACLE.read_text(encoding="utf-8")))
    assert not report.bad, (
        f"{len(report.bad)}/{report.ok + len(report.bad)} 불일치\n"
        + "\n".join(report.bad))
    assert report.ok > 150, f"대조 항목이 {report.ok}개뿐이다 — 표본이 줄었는지 확인할 것"
