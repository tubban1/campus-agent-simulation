"""Import real-world GeoJSON into the spatial truth layer."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.spatial.geo_importer import build_parser, import_real_world_geojson, load_geojson  # noqa: E402


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.world_key:
        parser.error("--world-key is required.")
    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            result = import_real_world_geojson(
                connection,
                load_geojson(args.geojson_path),
                world_key=args.world_key,
                origin_lat=args.origin_lat,
                origin_lon=args.origin_lon,
                dry_run=args.dry_run,
            )
    finally:
        engine.dispose()
    print(f"Import complete: {result.as_dict()}")


if __name__ == "__main__":
    main()
