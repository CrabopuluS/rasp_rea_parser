import sys
from pathlib import Path

# Делает корень репозитория доступным для импортов модулей bot/schedule_parser
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
