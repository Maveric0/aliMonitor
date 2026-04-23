import unittest

import failover_realm as core
import failover_webui_app as webui


class PortValidationTests(unittest.TestCase):
    def test_webui_parse_port_accepts_integer_values(self):
        self.assertEqual(webui.parse_port(443, "listen_port"), 443)
        self.assertEqual(webui.parse_port("443", "listen_port"), 443)

    def test_webui_parse_port_rejects_non_integer_values(self):
        for value in (1.9, True, "1.9", "12abc"):
            with self.subTest(value=value):
                with self.assertRaises(webui.ApiError):
                    webui.parse_port(value, "listen_port")

    def test_settings_forward_rules_accept_integer_values(self):
        rules = core.normalize_frontend_forward_rules(
            [{"listen_port": "80", "remote_host": "1.2.3.4", "remote_port": 8080}],
            "speedtest.example.com",
        )

        self.assertEqual(rules[0]["listen_port"], 80)
        self.assertEqual(rules[0]["remote_port"], 8080)

    def test_settings_forward_rules_reject_non_integer_values(self):
        invalid_rules = [
            {"listen_port": 1.9, "remote_host": "1.2.3.4", "remote_port": 8080},
            {"listen_port": True, "remote_host": "1.2.3.4", "remote_port": 8080},
            {"listen_port": "12abc", "remote_host": "1.2.3.4", "remote_port": 8080},
            {"listen_port": 80, "remote_host": "1.2.3.4", "remote_port": "8.8"},
        ]
        for rule in invalid_rules:
            with self.subTest(rule=rule):
                with self.assertRaises(RuntimeError):
                    core.normalize_frontend_forward_rules([rule], "speedtest.example.com")


if __name__ == "__main__":
    unittest.main()
