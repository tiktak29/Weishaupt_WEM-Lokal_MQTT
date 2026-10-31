"""Official HA Core cold-start verification from retained v1.1.3 messages.
Must run after test_issue14_ha_core.py on the SAME isolated Mosquitto broker.
Checks fresh Home Assistant discovery and state processing without the
legacy v1.1.2 update transition.
"""
import asyncio
import logging
from pathlib import Path
import tempfile

from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
import test_issue14_ha_core as testcore
from test_issue14_v113 import load_real_functions, NEW

LOG = logging.getLogger("wem_issue14_cold")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_, testcore.ns_new, testcore.events_new = load_real_functions(NEW)

async def cold_start():
    errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    def exception_handler(loop, context):
        errors.append(str(context))
        if previous_handler:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)
    loop.set_exception_handler(exception_handler)

    try:
        with tempfile.TemporaryDirectory(prefix="wem_issue14_cold_") as temporary:
            config_dir = Path(temporary)
            (config_dir / "configuration.yaml").write_text(
                "homeassistant:\n  name: Issue 14 Cold Start\n", encoding="utf-8"
            )
            hass = HomeAssistant(str(config_dir))
            loader.async_setup(hass)
            result = await bootstrap.async_from_config_dict({"homeassistant": {}}, hass)
            assert result is hass
            try:
                flow = await hass.config_entries.flow.async_init(
                    "mqtt", context={"source": "user"}
                )
                assert flow["type"] == "form" and flow["step_id"] == "broker", flow
                configured = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "broker": testcore.HOST,
                        "port": testcore.PORT,
                        "protocol": "5",
                        "other_settings": {
                            "set_client_cert": False,
                            "set_ca_cert": "off",
                            "transport": "tcp",
                        },
                    },
                )
                assert configured["type"] == "create_entry", configured
                await testcore.wait_for(
                    lambda: [entry for entry in hass.config_entries.async_entries("mqtt")
                             if entry.state == ConfigEntryState.LOADED],
                    "cold MQTT integration setup", 90
                )
                ids = await testcore.wait_for(
                    lambda: (d if len(d := testcore.sensor_entries(hass)) == len(testcore.KEYS) else None),
                    "cold retained v1.1.3 sensor registry", 90
                )
                expected = {
                    "Verdichter": ("Hz", "45", "measurement"),
                    "Soll Frequenz Verdichter": ("Hz", "52", "measurement"),
                    "Ist Frequenz Verdichter": ("Hz", "43", "measurement"),
                    "Drehzahl Pumpe M1": ("%", "55", "measurement"),
                    "Betriebsstd. Verdichter": ("h", "4388", "total_increasing"),
                    "Vorlauftemperatur": ("°C", "35", "measurement"),
                }
                for key, (unit, value, state_class) in expected.items():
                    await testcore.wait_for(
                        lambda k=key, u=unit, v=value, cl=state_class:
                            testcore.require_state(hass, ids[k], u, v, cl),
                        f"cold-start retained {key}={value} {unit}",
                        60
                    )
                    LOG.info("Cold start OK: %s => %s %s (%s)", key, value, unit, ids[key])
                assert len(set(ids.values())) == len(ids), ids
                await asyncio.sleep(1)
                assert not errors, f"Unexpected asyncio errors during clean v1.1.3 discovery: {errors}"
                LOG.info("PASS: fresh official HA Core with retained v1.1.3 config, no unhandled errors and no duplicate entity IDs")
            finally:
                await hass.async_stop(force=True)
    finally:
        loop.set_exception_handler(previous_handler)

if __name__ == "__main__":
    asyncio.run(cold_start())
