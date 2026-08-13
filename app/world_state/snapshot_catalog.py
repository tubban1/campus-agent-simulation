"""Snapshot table catalogs and schema capability groups."""

SNAPSHOT_STATE_TABLES = {
    "simulation_state": "key",
    "campus_state": "day",
    "campus_spaces": "code",
    "campus_events": "id",
    "residents": "id",
    "agent_profiles": "resident_id",
    "inventory": "id",
    "transactions": "id",
    "relationships": "from_resident_id, to_resident_id",
    "relationship_dynamics": "from_resident_id, to_resident_id",
    "long_term_goals": "id",
    "agent_goals": "id",
    "goal_dependencies": "id",
    "goal_revisions": "id",
    "campus_organizations": "id",
    "organization_members": "organization_id, resident_id",
    "agent_commitments": "id",
    "plan_outcomes": "id",
    "trajectory_episodes": "id",
    "group_goals": "id",
    "memories": "id",
    "agent_learning": "id",
    "collaborations": "id",
    "competitions": "id",
    "external_information": "id",
    "agent_information": "information_id, resident_id",
    "agent_action_plans": "id",
    "agent_news_posts": "id",
    "relationship_change_events": "id",
    "social_interaction_events": "id",
    "social_relation_interpretations": "id",
    "social_beliefs": "id",
    "policies": "id",
    "world_runtime": "id",
    "campus_schedule_rules": "id",
    "world_causal_weights": "id",
    "world_action_rules": "id",
    "world_delayed_effects": "due_at, id",
    "world_update_schedules": "id",
    "world_resource_accounts": "id",
}
SPATIAL_FOUNDATION_SNAPSHOT_TABLES = {
    "spatial_nodes": "id",
    "spatial_edges": "id",
    "agent_spatial_capabilities": "resident_id",
    "agent_spatial_states": "resident_id",
}

SPATIAL_SNAPSHOT_STATE_TABLES = {
    **SPATIAL_FOUNDATION_SNAPSHOT_TABLES,
    "spatial_resources": "id",
    "spatial_admission_queue": "node_id, queue_position",
}

BODY_SNAPSHOT_STATE_TABLES = {
    "agent_body_states": "resident_id",
}

PERCEPTION_SNAPSHOT_STATE_TABLES = {
    "agent_observations": "id",
    "agent_belief_states": "id",
    "agent_spatial_memories": "id",
}

CAPABILITY_SNAPSHOT_STATE_TABLES = {
    "agent_capability_profiles": "resident_id",
    "agent_opportunity_access": "id",
}

ECONOMY_SNAPSHOT_STATE_TABLES = {
    "economic_actors": "id",
    "ledger_accounts": "id",
    "ledger_transactions": "id",
    "ledger_entries": "id",
}

ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES = {
    "ledger_authorization_rules": "id",
    "ledger_authorized_operations": "transaction_id",
    "ledger_reversals": "original_transaction_id",
    "ledger_audit_events": "id",
}

ORGANIZATION_SNAPSHOT_STATE_TABLES = {
    "organization_runtime_profiles": "organization_id",
    "organization_roles": "id",
    "organization_role_assignments": "organization_id, resident_id",
    "organization_proposals": "id",
    "organization_votes": "proposal_id, resident_id",
    "organization_commitments": "id",
    "organization_relationships": "from_organization_id, to_organization_id",
    "organization_events": "id",
}

SUPPLY_SNAPSHOT_STATE_TABLES = {
    "catalog_items": "id",
    "inventory_accounts": "id",
    "production_recipes": "id",
    "production_recipe_inputs": "recipe_id, item_id",
    "production_batches": "id",
    "inventory_movements": "id",
    "service_offerings": "id",
    "service_deliveries": "id",
}

LABOR_SNAPSHOT_STATE_TABLES = {
    "labor_positions": "id",
    "employment_contracts": "id",
    "labor_shifts": "id",
    "income_programs": "id",
    "income_payments": "id",
    "expense_obligations": "id",
}

BUDGET_SNAPSHOT_STATE_TABLES = {
    "household_budget_profiles": "resident_id",
    "household_budget_snapshots": "id",
    "savings_transfers": "id",
    "choice_evaluations": "id",
}

MARKET_SNAPSHOT_STATE_TABLES = {
    "market_mechanisms": "id",
    "market_price_snapshots": "id",
    "market_demand_signals": "id",
    "market_friction_events": "id",
}

CREDIT_SNAPSHOT_STATE_TABLES = {
    "savings_goals": "id",
    "household_risk_profiles": "resident_id",
    "economic_shocks": "id",
    "risk_pool_claims": "id",
    "credit_products": "id",
    "credit_profiles": "resident_id",
    "credit_contracts": "id",
    "credit_installments": "id",
    "credit_payments": "id",
    "credit_events": "id",
}

PUBLIC_POLICY_SNAPSHOT_STATE_TABLES = {
    "public_services": "id",
    "public_service_operations": "id",
    "public_service_usages": "id",
    "externality_events": "id",
    "externality_exposures": "id",
    "policy_instruments": "id",
    "policy_benefits": "id",
    "policy_outcome_snapshots": "id",
}

SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES = {
    "communication_channels": "id",
    "information_claims": "id",
    "information_versions": "id",
    "information_transmissions": "id",
    "information_exposures": "id",
    "information_beliefs": "resident_id, claim_id",
    "institutional_rules": "id",
    "institutional_cases": "id",
    "institutional_decisions": "id",
    "resident_power_profiles": "resident_id",
    "institutional_trust_events": "id",
}
MACRO_SNAPSHOT_STATE_TABLES = {
    "macro_metric_definitions": "id",
    "macro_snapshots": "id",
    "macro_metric_values": "id",
    "macro_metric_components": "id",
    "macro_reconciliation_checks": "id",
}

ADAPTATION_SNAPSHOT_STATE_TABLES = {
    "constraint_rules": "id",
    "constraint_evaluations": "id",
    "boundary_attempts": "id",
    "constraint_consequences": "id",
    "experience_records": "id",
    "adaptive_memories": "id",
    "memory_revisions": "id",
    "strategy_states": "id",
    "learning_updates": "id",
    "norm_signals": "id",
    "norm_candidates": "id",
    "norm_evidence": "id",
    "agent_norm_beliefs": "resident_id, norm_id",
    "norm_state_transitions": "id",
    "norm_responses": "id",
    "rule_primitives": "id",
    "institutional_rule_proposals": "id",
    "rule_deliberations": "id",
    "evolved_rule_versions": "id",
    "rule_effect_reviews": "id",
}

RESILIENCE_SNAPSHOT_STATE_TABLES = {
    "shock_definitions": "id",
    "shock_instances": "id",
    "shock_impacts": "id",
    "resident_shock_exposures": "id",
    "recovery_actions": "id",
    "shock_state_transitions": "id",
}

POPULATION_SNAPSHOT_STATE_TABLES = {
    "population_profiles": "resident_id",
    "population_events": "id",
    "resident_role_assignments": "id",
    "resident_residency_periods": "id",
    "membership_transitions": "id",
    "population_effects": "id",
}

EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES = {
    "external_sources": "id",
    "external_sync_runs": "id",
    "external_raw_observations": "id",
    "external_source_locks": "source_id",
    "external_event_catalog": "event_type",
    "external_events": "id",
    "external_event_links": "id",
    "external_data_snapshots": "id",
    "external_snapshot_items": "snapshot_id, external_event_id",
    "external_runtime_modes": "branch_key",
    "external_exposures": "id",
    "external_replay_deliveries": "id",
    "external_impact_rules": "id",
    "external_event_impacts": "id",
    "external_state_reconciliations": "id",
    "external_governance_reviews": "id",
    "external_access_audit": "id",
    "external_runtime_health": "branch_key",
    "external_snapshot_exports": "id",
    "external_experiment_bindings": "id",
}

LONGITUDINAL_SNAPSHOT_STATE_TABLES = {
    "longitudinal_profiles": "resident_id",
    "life_course_stages": "id",
    "life_turning_points": "id",
    "path_dependency_links": "id",
    "longitudinal_aggregations": "id",
    "trajectory_reconciliations": "id",
}
