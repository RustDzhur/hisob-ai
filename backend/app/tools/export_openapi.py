"""Выгрузка OpenAPI-схемы: python -m app.tools.export_openapi > docs/api/openapi.json"""

import json
import sys

from app.main import app


def main() -> None:
    json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
