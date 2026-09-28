"""friendsend/lib/ui/theme/tokens.g.dart must be exactly what the generator produces from the design tokens."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_dart_tokens_are_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "design" / "prototype" / "gen_dart_tokens.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, "tokens.g.dart drifted from design/final_design_tokens.json; run design/prototype/gen_dart_tokens.py"
