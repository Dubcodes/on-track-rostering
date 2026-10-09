from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: validate_compose_image_contract.py CONFIG SERVICE_A SERVICE_B")
    config_path, first_name, second_name = sys.argv[1:]
    services = json.loads(Path(config_path).read_text(encoding="utf-8")).get("services", {})
    first = services.get(first_name, {})
    second = services.get(second_name, {})
    if not first or not second:
        raise SystemExit(f"missing required services: {first_name}, {second_name}")
    if "build" in first or "build" in second:
        raise SystemExit("application services must not define build blocks")
    first_image, second_image = first.get("image"), second.get("image")
    if not first_image or first_image != second_image:
        raise SystemExit("application and scheduler must use the same image")
    print(f"shared image contract OK: {first_image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
