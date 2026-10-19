"""The example model, which new-product.py copies as every product's starter.

Run with Python 3.12+:
    python -m unittest tests.test_example_model -v
"""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExampleModel(unittest.TestCase):
    def test_a_channel_parameter_picks_from_the_reported_channels(self):
        """A command parameter that names a channel is a child_id of that channel
        type, so OpenAVC offers the channels the product reports. A plain
        integer offers the type's whole ID range, channels the product may not
        have."""
        model = json.loads((ROOT / "product.json").read_text())
        types = model.get("child_entity_types", {})
        addressed = 0
        for command, definition in model["commands"].items():
            for name, rule in definition.get("params", {}).items():
                if name not in types:
                    continue
                addressed += 1
                with self.subTest(command=command, parameter=name):
                    self.assertEqual(rule.get("type"), "child_id")
                    self.assertEqual(rule.get("child_type"), name)
        self.assertGreater(addressed, 0)


if __name__ == "__main__":
    unittest.main()
