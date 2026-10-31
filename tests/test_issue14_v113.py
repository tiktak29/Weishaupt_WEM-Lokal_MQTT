"""Isolated regression tests. The real v1.1.2/v1.1.3 Python functions
are extracted from source by ast and executed with a mocked MQTT publisher.
No WebIF, network, heat pump, or live Home Assistant is contacted.
Run: python -m unittest discover -s tests -p 'test_issue14_v113.py' -v
Set WEM_V112_SRC to the path of the baseline script (checked out tag v1.1.2).
"""
import ast
import copy
import json
import os
import pathlib
import unittest
import unicodedata
from jinja2 import Environment, StrictUndefined

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = pathlib.Path(os.environ.get("WEM_V112_SRC", ROOT / "_baseline_v112" / "wem_mqtt_unified" / "script.py"))
NEW = ROOT / "wem_mqtt_unified" / "script.py"
JINJA = Environment(undefined=StrictUndefined)
FUNCTIONS = ("normalize_id", "build_clean_data", "publish_discovery")
TOPICS = {
    "MQTT_BASE": "homeassistant",
    "MQTT_STATE_TOPIC": "wem/wem_lokal_info",
    "AVAILABILITY_TOPIC": "wem/availability",
    "SYSTEM_STATUS_TOPIC": "wem/system_status",
    "LAST_UPDATE_TOPIC": "wem/last_update",
    "DAILY_SUCCESS_TOPIC": "wem/daily_success",
    "DAILY_SUCCESS_ATTR_TOPIC": "wem/daily_success_attributes",
}

def load_real_functions(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    methods = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS]
    if {x.name for x in methods} != set(FUNCTIONS):
        raise AssertionError(f"Function mismatch in {path}")
    events = []
    def mqtt_publish(topic, payload, retain=True):
        events.append((topic, copy.deepcopy(payload), retain))
    ns = dict(TOPICS, unicodedata=unicodedata, mqtt_publish=mqtt_publish)
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), ns)
    return source, ns, events

def get_discovery(ns, events, section, key, initial):
    events.clear()
    ns["publish_discovery"]({section: {key: initial}})
    matching = [item[1] for item in events if item[0].endswith(f"/{ns['normalize_id'](key)}/config")]
    if len(matching) != 1:
        raise AssertionError((section, key, initial, len(matching)))
    return matching[0]

def render_state(ns, payload, section, key, raw):
    state = ns["build_clean_data"]({section: {key: raw}})
    return JINJA.from_string(payload["value_template"]).render(value_json={"WEM-Lokal Info": state})

class Issue14Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oldsrc, cls.old, cls.old_events = load_real_functions(OLD)
        cls.newsrc, cls.new, cls.new_events = load_real_functions(NEW)

    def test_python_source_compiles(self):
        compile(self.oldsrc, str(OLD), "exec")
        compile(self.newsrc, str(NEW), "exec")

    def test_other_functions_unmodified(self):
        def other_ast(src):
            tree = ast.parse(src)
            tree.body = [x for x in tree.body if not (isinstance(x, ast.FunctionDef) and x.name == "publish_discovery")]
            # Known startup version update is intentionally allowed.
            for x in ast.walk(tree):
                if isinstance(x, ast.Constant) and isinstance(x.value, str):
                    x.value = x.value.replace("Starting Weishaupt WEM-Lokal MQTT v1.1.3", "Starting Weishaupt WEM-Lokal MQTT v1.1.2")
            return ast.dump(tree, include_attributes=False)
        self.assertEqual(other_ast(self.oldsrc), other_ast(self.newsrc))

    def test_issue14_bug_reproduced_and_fixed(self):
        old = get_discovery(self.old, self.old_events, "Wärmepumpe", "Verdichter", "0 Hz")
        new = get_discovery(self.new, self.new_events, "Wärmepumpe", "Verdichter", "0 Hz")
        self.assertEqual((old["unit_of_measurement"], old["state_class"]), ("h", "total_increasing"))
        self.assertEqual((new["unit_of_measurement"], new["state_class"]), ("Hz", "measurement"))
        self.assertEqual(render_state(self.new, new, "Wärmepumpe", "Verdichter", "45 Hz"), "45")
        self.assertEqual(render_state(self.new, new, "Wärmepumpe", "Verdichter", "Aus"), "0")

    def test_named_compressor_frequency_off_first(self):
        for key in ["Soll Frequenz Verdichter", "Ist Frequenz Verdichter",
                    "Test Ist Frequenz Verdichter Zusatz", "FREQUENZ VERDICHTER"]:
            with self.subTest(name=key):
                new = get_discovery(self.new, self.new_events, "Wärmepumpe", key, "Aus")
                self.assertEqual(new["unit_of_measurement"], "Hz")
                self.assertEqual(new["state_class"], "measurement")
                for raw, expected in [("Aus", "0"), ("0 Hz", "0"), ("55 Hz", "55"),
                                      ("45 hz", "45"), ("45 HZ", "45"), ("45 hZ", "45"), ("45,5 Hz", "45.5")]:
                    with self.subTest(raw=raw):
                        self.assertEqual(render_state(self.new, new, "Wärmepumpe", key, raw), expected)

    def test_no_false_positive_for_counters(self):
        cases = [
            ("Betriebsstd. Frequenz Verdichter", "7702 h", "h", "total_increasing"),
            ("Betriebsstd. Frequenz Verdichter", "Aus", None, None),
            ("Betriebsstunden Frequenz Verdichter", "Aus", None, None),
            ("Schaltspiele Frequenz Verdichter", "15", None, "total_increasing"),
            ("Ist Frequenz Verdichter", "2500 rpm", "rpm", "measurement"),
            ("Verdichter", "0 rpm", "rpm", "measurement"),
        ]
        for key, initial, unit, stateclass in cases:
            with self.subTest(name=key, value=initial):
                new = get_discovery(self.new, self.new_events, "Wärmepumpe", key, initial)
                self.assertEqual(new.get("unit_of_measurement"), unit)
                self.assertEqual(new.get("state_class"), stateclass)

    def test_unchanged_broad_existing_sensors(self):
        cases = [
            ("Wärmepumpe", "Verdichter", "2163 rpm"),
            ("Wärmepumpe", "Verdichter", "0 rpm"),
            ("Wärmepumpe", "Verdichter", "Aus"),
            ("Wärmepumpe", "Drehzahl Pumpe M1", "Aus"),
            ("Wärmepumpe", "Drehzahl Pumpe M1", "55 %"),
            ("Wärmepumpe", "Leistungsanforderung", "Aus"),
            ("Wärmepumpe", "Leistungsanforderung", "35 %"),
            ("Wärmepumpe", "Betriebsstd. Verdichter", "4388 h"),
            ("Wärmepumpe", "Schaltspiele Verdichter", "2681"),
            ("Wärmepumpe", "Niederdruck", "4.4 BAR"),
            ("Wärmepumpe", "Soll Leistung", "2.6 KW"),
            ("Wärmepumpe", "Warmwassertemperatur", "43.5 °C"),
            ("Wärmepumpe", "Volumenstrom", "1.2m3/h"),
            ("Wärmepumpe", "AT Mittelwert", "8.0 K"),
            ("Heizkreis 1", "Vorlauftemperatur", "31.5 °C"),
            ("Heizkreis 2", "Pumpe", "Ein"),
            ("Statistik", "elektrische Energie Tag", "7.158 KWh"),
            ("2. WEZ", "Status", "Aus"),
        ]
        for sec, key, value in cases:
            with self.subTest(sec=sec, key=key, value=value):
                self.assertEqual(
                    get_discovery(self.old, self.old_events, sec, key, value),
                    get_discovery(self.new, self.new_events, sec, key, value))
                self.assertEqual(self.old["build_clean_data"]({sec: {key:value}}),
                                 self.new["build_clean_data"]({sec: {key:value}}))

    def test_mqtt_ids_and_retained_config_replaced(self):
        for sec, key, value in [
            ("Wärmepumpe", "Verdichter", "0 Hz"),
            ("Wärmepumpe", "Ist Frequenz Verdichter", "Aus"),
            ("Wärmepumpe", "Verdichter", "0 rpm"),
        ]:
            with self.subTest(key=key, initial=value):
                old = get_discovery(self.old, self.old_events, sec, key, value)
                new = get_discovery(self.new, self.new_events, sec, key, value)
                for field in ("uniq_id", "name", "state_topic", "availability_topic", "device"):
                    self.assertEqual(old[field], new[field])
                self.assertTrue(self.old_events[-1][2])
                self.assertTrue(self.new_events[-1][2])
                self.assertEqual(self.old_events[-1][0], self.new_events[-1][0])
                retained = {self.old_events[-1][0]: old}
                retained[self.new_events[-1][0]] = new  # offline retained-message replacement
                self.assertEqual(retained[self.new_events[-1][0]], new)

    def test_known_limitations_remain_documented(self):
        p = get_discovery(self.new, self.new_events, "Wärmepumpe", "Verdichter", "Aus")
        self.assertIsNone(p.get("unit_of_measurement"))  # bare name still unresolved
        p = get_discovery(self.new, self.new_events, "Wärmepumpe", "Drehzahl Pumpe M1", "55 %")
        self.assertEqual(render_state(self.new, p, "Wärmepumpe", "Drehzahl Pumpe M1", "Stop"), "Stop")

if __name__ == "__main__":
    unittest.main()
