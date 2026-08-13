"""Seed the stable campus topology and backfill Agent spatial truth."""

from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database_engine  # noqa: E402
from app.spatial.seed import seed_spatial_foundation  # noqa: E402


def main() -> None:
    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            result = seed_spatial_foundation(
                connection,
                real_world_key=os.getenv("INITIAL_WORLD_KEY") or None,
            )
    finally:
        engine.dispose()
    print(
        "Spatial foundation ready: "
        f"nodes={result['nodes_total']} (+{result['nodes_created']}), "
        f"edges={result['edges_total']} (+{result['edges_created']}), "
        f"resources={result['resources_total']} (+{result['resources_created']}), "
        f"states=+{result['states_created']}, "
        f"body_states=+{result['body_states_created']}, "
        f"spatial_capabilities=+{result['capabilities_created']}, "
        f"capability_profiles=+{result['capability_profiles_created']}, "
        f"opportunities=+{result['opportunities_created']}."
    )


if __name__ == "__main__":
    main()
