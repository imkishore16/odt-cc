"""ODT-CC — every project path in one place, so scripts work from any directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENUE_FILE = ROOT / "config" / "venue.json"
SCENARIOS_FILE = ROOT / "config" / "scenarios.json"
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"
USD_DIR = ROOT / "usd"              # twin_<scenario>.usda — the files you open in Kit
LAYER_DIR = USD_DIR / "layers"      # venue_layout.usda + crowd_<scenario>.usda
RENDER_DIR = ROOT / "renders"


def egress_csv(scenario):
    return DATA_DIR / f"egress_{scenario}.csv"
