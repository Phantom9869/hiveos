import sys
from pathlib import Path

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "backend"))

# Pre-import backend.shared.* then alias them as 'shared.*'
# so that router/app.py's `from shared import state` resolves
# to the exact same objects the tests patch under 'backend.shared.*'
import backend.shared
import backend.shared.state
import backend.shared.scheduler
import backend.shared.broadcast

sys.modules["shared"] = backend.shared
sys.modules["shared.state"] = backend.shared.state
sys.modules["shared.scheduler"] = backend.shared.scheduler
sys.modules["shared.broadcast"] = backend.shared.broadcast