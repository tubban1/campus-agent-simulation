import importlib
import unittest

import app.main


RUNTIME_MODULES = (
    ("app.world_runtime.social_runtime", "configure"),
    ("app.world_runtime.tick_runtime", "configure"),
    ("app.world_runtime.update_scheduler", "configure"),
    ("app.world_runtime.causal_actions", "configure"),
    ("app.world_runtime.action_execution", "configure"),
    ("app.world_runtime.environment_config", "configure"),
    ("app.world_runtime.remaining_runtime", "configure"),
    ("app.world_runtime.state_environment", "configure"),
    ("app.world_runtime.planning_decision", "configure"),
    ("app.world_state.runtime_schema", "configure"),
    ("app.world_state.snapshot_service", "configure"),
    ("services.newspaper", "configure_runtime"),
)


class RuntimeBindingSafetyTest(unittest.TestCase):
    def test_runtime_configuration_preserves_module_identity(self):
        for module_name, configure_name in RUNTIME_MODULES:
            module = importlib.import_module(module_name)
            getattr(module, configure_name)(**app.main.__dict__)
            self.assertEqual(module.__name__, module_name)


if __name__ == "__main__":
    unittest.main()
