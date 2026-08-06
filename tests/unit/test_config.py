from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from kitdev_sandboxes.config import (
    DEFAULT_CONFIG_PATH,
    ConfigurationError,
    DeploymentProfile,
    LifecycleMode,
    load_configuration,
    parse_yaml,
    validate_configuration,
)


def default_mapping() -> dict[str, object]:
    return parse_yaml(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


class ConfigurationTests(unittest.TestCase):
    def test_defaults_load_into_typed_configuration(self) -> None:
        loaded = load_configuration(installed_path=Path("/definitely/not/installed.yaml"))

        self.assertEqual(loaded.configuration.schema_version, 1)
        self.assertIs(loaded.configuration.deployment.profile, DeploymentProfile.MINIMAL)
        self.assertIs(loaded.configuration.deployment.lifecycle_mode, LifecycleMode.PRODUCTION)

    def test_partial_operator_config_and_cli_override_merge_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            operator = Path(directory) / "operator.yaml"
            operator.write_text("deployment:\n  profile: standard\n", encoding="utf-8")
            loaded = load_configuration(
                config_path=operator,
                cli_overrides={"deployment": {"lifecycle_mode": "migration"}},
            )

        self.assertIs(loaded.configuration.deployment.profile, DeploymentProfile.STANDARD)
        self.assertIs(loaded.configuration.deployment.lifecycle_mode, LifecycleMode.MIGRATION)

    def test_unknown_keys_are_rejected_after_merge(self) -> None:
        data = default_mapping()
        deployment = data["deployment"]
        assert isinstance(deployment, dict)
        deployment["surprise"] = True

        with self.assertRaisesRegex(ConfigurationError, "unknown keys: surprise"):
            validate_configuration(data)

    def test_unsafe_project_paths_are_rejected(self) -> None:
        unsafe_paths = [
            "/",
            "/../etc",
            "/tmp/kitdev-sandboxes",
            "/var/lib/kitdev-sandboxes///",
            "/var/lib/kitdev-sandboxes/../other",
            "/var/lib/kitdev-sandboxes/bad\x01path",
            "/var/lib/kitdev-sandboxes/space path",
            "/var/lib/kitdev-sandboxes/ünicode",
        ]
        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path):
                data = default_mapping()
                paths = data["paths"]
                assert isinstance(paths, dict)
                paths["state"] = unsafe_path
                with self.assertRaises(ConfigurationError):
                    validate_configuration(data)

    def test_deep_yaml_is_a_configuration_error_not_recursion_error(self) -> None:
        text = "\n".join(f"{'  ' * index}key{index}:" for index in range(40)) + "\n"

        with self.assertRaisesRegex(ConfigurationError, "nesting exceeds"):
            parse_yaml(text)

    def test_large_yaml_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "exceeds"):
            parse_yaml("#" + ("x" * 1_048_576))

    def test_fifo_configuration_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "operator.yaml"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(ConfigurationError, "not a regular file"):
                load_configuration(config_path=fifo)

    def test_network_and_public_name_values_are_structurally_validated(self) -> None:
        mutations = [
            ("listen_address", "localhost"),
            ("ipv4_cidr", "172.31.0.1/16"),
            ("dns_resolvers", ["resolver.invalid"]),
            ("private_egress_allowlist", ["10.0.0.1/8"]),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                data = default_mapping()
                section_name = "deployment" if key == "listen_address" else "network"
                section = data[section_name]
                assert isinstance(section, dict)
                section[key] = value
                with self.assertRaises(ConfigurationError):
                    validate_configuration(data)

        data = default_mapping()
        deployment = data["deployment"]
        assert isinstance(deployment, dict)
        deployment.update(public_exposure=True, domain="not a fqdn")
        with self.assertRaises(ConfigurationError):
            validate_configuration(data)

    def test_private_listen_address_must_be_loopback(self) -> None:
        for address in ("0.0.0.0", "::", "192.0.2.10", "2001:db8::1"):
            with self.subTest(address=address):
                data = default_mapping()
                deployment = data["deployment"]
                assert isinstance(deployment, dict)
                deployment["listen_address"] = address
                with self.assertRaisesRegex(ConfigurationError, "must be loopback"):
                    validate_configuration(data)

        for address in ("127.0.0.1", "127.12.34.56", "::1"):
            with self.subTest(address=address):
                data = default_mapping()
                deployment = data["deployment"]
                assert isinstance(deployment, dict)
                deployment["listen_address"] = address
                self.assertEqual(
                    validate_configuration(data).deployment.listen_address,
                    address,
                )

        data = default_mapping()
        deployment = data["deployment"]
        assert isinstance(deployment, dict)
        deployment.update(
            public_exposure=True,
            domain="sandboxes.example.com",
            listen_address="0.0.0.0",
        )
        self.assertEqual(validate_configuration(data).deployment.listen_address, "0.0.0.0")

    def test_schema_and_typed_contract_have_matching_keys_enums_and_path_guards(self) -> None:
        schema = json.loads((DEFAULT_CONFIG_PATH.parent / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]), set(default_mapping()))
        deployment = schema["properties"]["deployment"]
        self.assertEqual(set(deployment["properties"]), set(default_mapping()["deployment"]))
        self.assertEqual(
            set(deployment["properties"]["lifecycle_mode"]["enum"]),
            {mode.value for mode in LifecycleMode},
        )
        self.assertEqual(
            set(deployment["properties"]["profile"]["enum"]),
            {profile.value for profile in DeploymentProfile},
        )
        path_schema = schema["properties"]["paths"]["properties"]
        for key, path_contract in path_schema.items():
            self.assertIsNone(re.fullmatch(path_contract["pattern"], "/"), key)
            self.assertIsNone(re.fullmatch(path_contract["pattern"], "/../etc"), key)
        private_listen_rule = schema["allOf"][1]
        self.assertEqual(
            private_listen_rule["if"]["properties"]["deployment"]["properties"][
                "public_exposure"
            ]["const"],
            False,
        )
        loopback_options = private_listen_rule["then"]["properties"]["deployment"][
            "properties"
        ]["listen_address"]["anyOf"]
        self.assertTrue(any(option.get("const") == "::1" for option in loopback_options))
        ipv4_pattern = next(option["pattern"] for option in loopback_options if "pattern" in option)
        self.assertIsNotNone(re.match(ipv4_pattern, "127.0.0.1"))
        self.assertIsNone(re.match(ipv4_pattern, "0.0.0.0"))

    def test_loaded_sources_are_metadata_not_merged_configuration(self) -> None:
        loaded = load_configuration(installed_path=Path("/definitely/not/installed.yaml"))

        self.assertNotIn("sources", loaded.merged)
        self.assertEqual(len(loaded.sources), 1)


if __name__ == "__main__":
    unittest.main()
