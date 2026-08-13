"""Import a real-world GeoJSON file into the spatial truth layer."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.spatial.geo_importer import build_parser, import_real_world_geojson, load_geojson  # noqa: E402


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    world_key = args.world_key or args.campus_key
    if not world_key:
        parser.error("--world-key is required.")
    payload = load_geojson(args.geojson_path)
    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            summary = import_real_world_geojson(
                connection,
                payload,
                world_key=world_key,
                origin_lat=args.origin_lat,
                origin_lon=args.origin_lon,
                dry_run=args.dry_run,
            )
    finally:
        engine.dispose()
    prefix = "Dry run" if args.dry_run else "Import complete"
    print(f"{prefix}: {summary.as_dict()}")


if __name__ == "__main__":
    main()
