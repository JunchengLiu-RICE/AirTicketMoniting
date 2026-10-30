"""Prepare the runtime configuration from trip.yaml; secrets stay on the runner."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import build_config
from core.platforms import BROWSER_PLATFORMS


def main():
    settings = yaml.safe_load(Path("trip.yaml").read_text(encoding="utf-8"))
    config = build_config(settings)
    runtime = Path(".runtime")
    runtime.mkdir(exist_ok=True)
    (runtime / "started_at").write_text(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    (runtime / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    state = Path(".monitor-state/config.json")
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({"trip": settings}, default=str, indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"ready={str(bool(config['routes'])).lower()}\n"
                         f"needs_browser={str(bool(set(config['platforms']) & BROWSER_PLATFORMS)).lower()}\n")
    print(f"Active routes: {len(config['routes'])}; platforms: {', '.join(config['platforms'])}")


if __name__ == "__main__":
    main()
