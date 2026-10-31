"""Integration test using an isolated real Mosquitto broker on localhost:18883.
The production publish_discovery/build_clean_data functions are loaded via AST.
Nothing connects to a real WebIF or Home Assistant instance.
"""
import json
import os
import threading
import time
import unittest
from paho.mqtt import client as mqtt
from jinja2 import Environment, StrictUndefined
from test_issue14_v113 import OLD, NEW, load_real_functions

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEM_TEST_MQTT_PORT", "18883"))
JINJA = Environment(undefined=StrictUndefined)

def send_retained(events):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(HOST, PORT, 5)
    client.loop_start()
    try:
        for topic, obj, retain in events:
            value = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (dict, list)) else obj
            receipt = client.publish(topic, value, qos=1, retain=retain)
            receipt.wait_for_publish(timeout=5)
            if not receipt.is_published():
                raise AssertionError(f"MQTT publish failed: {topic}")
    finally:
        client.disconnect()
        client.loop_stop()

def retained_message(topic):
    finished = threading.Event()
    received = []
    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe(topic, qos=1)
    def on_message(client, userdata, msg):
        received.append((msg.topic, msg.payload.decode("utf-8"), msg.retain))
        finished.set()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(HOST, PORT, 5)
    client.loop_start()
    try:
        if not finished.wait(8):
            raise AssertionError(f"No retained MQTT message at {topic}")
        return received[0]
    finally:
        client.disconnect()
        client.loop_stop()

class RealBrokerRetainedUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.old, cls.old_events = load_real_functions(OLD)
        _, cls.new, cls.new_events = load_real_functions(NEW)

    def one_device_config(self, ns, events, key, initial):
        events.clear()
        ns["publish_discovery"]({"Wärmepumpe": {key: initial}})
        send_retained(events)
        config_topic = "homeassistant/sensor/wem_lokal_wp_" + ns["normalize_id"](key) + "/config"
        topic, contents, retained = retained_message(config_topic)
        self.assertTrue(retained)
        self.assertEqual(topic, config_topic)
        config = json.loads(contents)
        self.assertEqual(config["uniq_id"], "wem_lokal_wp_" + ns["normalize_id"](key))
        self.assertEqual(config["state_topic"], "wem/wem_lokal_info")
        return config_topic, config

    def emit_state(self, ns, key, value):
        state = {"WEM-Lokal Info": ns["build_clean_data"]({"Wärmepumpe": {key: value}})}
        send_retained([("wem/wem_lokal_info", state, True)])
        topic, contents, retained = retained_message("wem/wem_lokal_info")
        self.assertTrue(retained)
        self.assertEqual(topic, "wem/wem_lokal_info")
        return json.loads(contents)

    def test_real_qos1_reconnect_and_puback_before_clear(self):
        data = {"Wärmepumpe": {
            "Außengerät Variante": "WSB 6",
            "Verdichter": "45 Hz",
        }}
        paho = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        old_client = self.new["mqtt_client"]
        old_api = self.new["mqtt"]
        self.new["mqtt_client"] = paho
        self.new["mqtt"] = mqtt
        pending = {"ticket": None, "last_attempt": 0.0}
        try:
            # Disconnected: no publish, no lost one-shot update.
            self.assertIs(self.new["update_pending_verdichter_discovery"](
                data, pending, "Wärmepumpe"
            ), pending)
            self.assertIsNone(pending["ticket"])

            paho.connect(HOST, PORT, 5)
            paho.loop_start()
            try:
                limit = time.monotonic() + 8
                while not paho.is_connected() and time.monotonic() < limit:
                    time.sleep(0.05)
                self.assertTrue(paho.is_connected())
                self.assertIs(self.new["update_pending_verdichter_discovery"](
                    data, pending, "Wärmepumpe"
                ), pending)
                ticket = pending["ticket"]
                self.assertIsNotNone(ticket)
                self.assertEqual(ticket.rc, mqtt.MQTT_ERR_SUCCESS)
                ticket.wait_for_publish(timeout=8)
                self.assertTrue(ticket.is_published())
                self.assertIsNone(self.new["update_pending_verdichter_discovery"](
                    data, pending, "Wärmepumpe"
                ))
                topic, raw, retained = retained_message(
                    "homeassistant/sensor/wem_lokal_wp_verdichter/config"
                )
                self.assertTrue(retained)
                conf = json.loads(raw)
                self.assertEqual(conf["unit_of_measurement"], "Hz")
                self.assertEqual(conf["state_class"], "measurement")
            finally:
                paho.disconnect()
                paho.loop_stop()
        finally:
            self.new["mqtt_client"] = old_client
            self.new["mqtt"] = old_api

    def test_retained_discovery_upgrade_from_112_to_113(self):
        for key, first, next_value, base_unit, new_unit, expected in [
            ("Verdichter", "0 Hz", "45 Hz", "h", "Hz", "45"),
            ("Ist Frequenz Verdichter", "Aus", "43 hZ", None, "Hz", "43"),
            ("Soll Frequenz Verdichter", "Aus", "45 HZ", None, "Hz", "45"),
            ("Verdichter", "2163 rpm", "Aus", "rpm", "rpm", "0"),
        ]:
            with self.subTest(key=key, first=first):
                topic_old, conf_old = self.one_device_config(self.old, self.old_events, key, first)
                self.assertEqual(conf_old.get("unit_of_measurement"), base_unit)
                topic_new, conf_new = self.one_device_config(self.new, self.new_events, key, first)
                self.assertEqual(topic_new, topic_old)
                self.assertEqual(conf_new.get("unit_of_measurement"), new_unit)
                self.assertEqual(conf_new["state_class"], "measurement")
                state = self.emit_state(self.new, key, next_value)
                value = JINJA.from_string(conf_new["value_template"]).render(value_json=state)
                self.assertEqual(value, expected)
                # A newly connected subscriber sees the NEW retained config, not the old one.
                _, raw_config, retained = retained_message(topic_old)
                self.assertTrue(retained)
                self.assertEqual(json.loads(raw_config), conf_new)

if __name__ == "__main__":
    unittest.main()
