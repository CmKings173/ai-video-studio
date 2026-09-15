from __future__ import annotations

import json
from pathlib import Path

from apps.api.app.main import app


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    destination = root / "docs" / "openapi.yaml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
