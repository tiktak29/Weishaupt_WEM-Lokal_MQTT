"""Pre-release negative audit; intentionally fails where strict no-regression
requirements are NOT satisfied. Test-only branch, never install live.

Run: python -m unittest discover -s tests -p 'test_issue14_release_audit.py' -v
WEM_V112_SRC must reference original v1.1.2 GitHub tag checkout.
"""
import copy
import unittest
from test_issue14_v113 import OLD, NEW, load_real_functions, get_discovery


class ScopeAndDeliveryAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._oldsource, cls.old, cls.old_events = load_real_functions(OLD)
        cls._newsource, cls.new, cls.new_events = load_real_functions(NEW)

    def test_control_unchanged_non_compressor_temperature(self):
        original = get_discovery(self.old, self.old_events,
                                 "Wärmepumpe", "Vorlauftemperatur", "35 °C")
        candidate = get_discovery(self.new, self.new_events,
                                  "Wärmepumpe", "Vorlauftemperatur", "35 °C")
        self.assertEqual(original, candidate)

    def test_strict_scope_no_change_hour_text_elsewhere(self):
        # Synthetic edge case, not a claim this value exists on any model.
        # New endswith(" h") differs from old " h" in for non-compressor text.
        original = get_discovery(self.old, self.old_events,
                                 "Wärmepumpe", "Restlaufzeit", "12 h (gesamt)")
        candidate = get_discovery(self.new, self.new_events,
                                  "Wärmepumpe", "Restlaufzeit", "12 h (gesamt)")
        self.assertEqual(original, candidate,
                         "Out-of-scope hour-text discovery classification changed")

    def test_strict_scope_no_change_other_frequency_sensor(self):
        # Synthetic non-compressor Hz value: new branch matches ALL Hz suffixes.
        original = get_discovery(self.old, self.old_events,
                                 "Wärmepumpe", "Luefterdrehzahl", "45 Hz")
        candidate = get_discovery(self.new, self.new_events,
                                  "Wärmepumpe", "Luefterdrehzahl", "45 Hz")
        self.assertEqual(original, candidate,
                         "Out-of-scope Hz Discovery classification changed")

    def test_targeted_discovery_retries_when_publish_reports_disconnection(self):
        # Simulated observable publish failure (e.g. disconnected broker):
        # a one-shot pending flag must not be irrevocably cleared.
        data = {"Wärmepumpe": {
            "Außengerät Variante": "WSB 6",
            "Verdichter": "45 Hz",
        }}
        failures = []
        class FailedPublish:
            rc = 4  # Paho MQTT_ERR_NO_CONN
            def is_published(self):
                return False
        class FailingClient:
            def is_connected(self):
                return True
            def publish(self, topic, payload, qos=0, retain=True):
                failures.append((topic, payload, qos, retain))
                return FailedPublish()

        prior = self.new["mqtt_client"]
        self.new["mqtt_client"] = FailingClient()
        try:
            flag = {"ticket": None, "last_attempt": 0.0}
            pending = self.new["update_pending_verdichter_discovery"](
                data, flag, "Wärmepumpe"
            )
        finally:
            self.new["mqtt_client"] = prior
        self.assertEqual(len(failures), 1,
                         "Targeted Discovery should be one MQTT publish")
        self.assertEqual(failures[0][2:], (1, True),
                         "Targeted retained config must use QoS1")
        self.assertIs(pending, flag,
                      "A failed targeted Discovery publish must remain pending")

    def test_targeted_qos1_waits_for_puback(self):
        data = {"Wärmepumpe": {
            "Außengerät Variante": "WSB 6", "Verdichter": "45 Hz"
        }}
        calls = []
        class DelayedTicket:
            rc = 0
            confirmed = False
            def is_published(self):
                return self.confirmed
        receipt = DelayedTicket()
        class MqttClient:
            def is_connected(self):
                return True
            def publish(self, topic, payload, qos=0, retain=True):
                calls.append((topic, payload, qos, retain))
                return receipt
        old = self.new["mqtt_client"]
        self.new["mqtt_client"] = MqttClient()
        try:
            pending = {"ticket": None, "last_attempt": 0.0}
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2:], (1, True))
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertEqual(len(calls), 1)  # no resend while awaiting PUBACK
            receipt.confirmed = True
            self.assertIsNone(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ))
            self.assertEqual(len(calls), 1)
        finally:
            self.new["mqtt_client"] = old

    def test_targeted_discovery_resends_after_unacknowledged_timeout(self):
        data = {"Wärmepumpe": {
            "Außengerät Variante": "WSB 6", "Verdichter": "45 Hz"
        }}
        calls = []
        class NeverAck:
            rc = 0
            def is_published(self):
                return False
        class PendingClient:
            def is_connected(self):
                return True
            def publish(self, topic, payload, qos=0, retain=True):
                calls.append((topic, payload, qos, retain))
                return NeverAck()
        old = self.new["mqtt_client"]
        self.new["mqtt_client"] = PendingClient()
        try:
            pending = {"ticket": None, "last_attempt": 0.0}
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertEqual(len(calls), 1)
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertEqual(len(calls), 1)
            pending["last_attempt"] -= 31.0
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(item[2:] == (1, True) for item in calls))
        finally:
            self.new["mqtt_client"] = old

    def test_targeted_discovery_offline_does_not_clear_pending(self):
        data = {"Wärmepumpe": {"Verdichter": "2163 rpm"}}
        class Offline:
            def is_connected(self):
                return False
            def publish(self, *args, **kwargs):
                raise AssertionError("Must not publish when disconnected")
        old = self.new["mqtt_client"]
        self.new["mqtt_client"] = Offline()
        try:
            pending = {"ticket": None, "last_attempt": 0.0}
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertIsNone(pending["ticket"])
        finally:
            self.new["mqtt_client"] = old



if __name__ == "__main__":
    unittest.main()
