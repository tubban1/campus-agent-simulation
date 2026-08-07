"""Read response shaping for lifecycle endpoints."""


def lifecycle_overview(conn, resident_id, *, build_overview, **filters):
    return build_overview(conn, resident_id, **filters)


def lifecycle_events(conn, resident_id, *, build_overview, **filters):
    overview = build_overview(conn, resident_id, **filters)
    return {"analysis_version": overview["analysis_version"], "events": overview["timeline"], "research_boundaries": overview["research_boundaries"]}


def lifecycle_turning_points(conn, resident_id, *, build_overview, limit=12):
    overview = build_overview(conn, resident_id, limit=500)
    return {"analysis_version": overview["analysis_version"], "turning_points": overview["turning_points"][:min(max(limit, 1), 30)]}


def lifecycle_relationships(conn, resident_id, *, build_overview):
    overview = build_overview(conn, resident_id, limit=240)
    return {"analysis_version": overview["analysis_version"], "relationships": overview["relationships"], "history_available": overview["research_boundaries"]["relationship_history_available"]}


def lifecycle_groups(conn, resident_id, *, build_overview):
    overview = build_overview(conn, resident_id, limit=240)
    return {"analysis_version": overview["analysis_version"], "groups": overview["groups"], "membership_history_available": overview["research_boundaries"]["group_membership_history_available"]}
