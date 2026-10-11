"""pytest 根配置：把项目根注入 sys.path，使 tests 可直接 import app.*。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
