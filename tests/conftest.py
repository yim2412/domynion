"""`tools/` 를 import 할 수 있게 한다 — 대조 도구를 테스트가 재사용한다."""

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import verify_port                                    # noqa: E402
sys.modules.setdefault("tools_verify", verify_port)
