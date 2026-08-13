"""Import the repository's versioned Tsinghua OSM snapshot into a fresh world."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.spatial.geo_importer import import_real_world_geojson, load_geojson  # noqa: E402


def main() -> None:
    geojson_path = PROJECT_ROOT / "data" / "geo" / "tsinghua_main.geojson"
    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            result = import_real_world_geojson(
                connection, load_geojson(geojson_path), world_key="tsinghua_main"
            )
    finally:
        engine.dispose()
    print(f"Tsinghua real world imported: {result.as_dict()}")


if __name__ == "__main__":
    main()
