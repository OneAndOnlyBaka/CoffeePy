"""Home Assistant MQTT discovery for the bridge's own topics, so the same container can feed Home
Assistant - with CoffeePy or without it. Only describes what the bridge publishes anyway; HA reads
the values from the bridge's retained state and counters with value templates. Pure, tested in
coffee/tests/test_machine_bridge.py."""
import json

# The alerts worth an entity of their own: what someone has to do at the machine.
ALERT_SENSORS = (
    ("fill_water", "Fill water", "problem"),
    ("empty_grounds", "Empty grounds", "problem"),
    ("empty_tray", "Empty drip tray", "problem"),
    ("no_beans", "Beans empty", "problem"),
    ("insert_tray", "Drip tray missing", "problem"),
    ("error_status", "Error", "problem"),
    ("lockedkeys", "Keys locked", "lock"),
)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def device(state: dict) -> dict:
    return {
        "identifiers": [f"coffeepy_machine_{_slug(state.get('address', 'unknown'))}"],
        "name": f"JURA {state.get('model') or ''}".strip(),
        "manufacturer": "JURA",
        "model": state.get("model") or "",
        "sw_version": state.get("firmware") or "",
    }


def configs(prefix: str, discovery: str, state: dict, products: list[dict], brew_buttons: bool):
    """[(topic, payload)] - every entity's discovery config, published retained. `products` is
    [{"code", "name"}] as the bridge publishes them; buttons for them only with `brew_buttons`, as a
    brew from Home Assistant goes past the tally (it counts as a brew at the machine)."""
    dev = device(state)
    node = dev["identifiers"][0]
    availability = {"availability_topic": f"{prefix}/status", "payload_available": "online",
                    "payload_not_available": "offline"}
    out = []

    def add(component, object_id, config):
        config = {**config, **availability, "device": dev, "unique_id": f"{node}_{object_id}",
                  "object_id": f"{node}_{object_id}"}
        out.append((f"{discovery}/{component}/{node}/{object_id}/config", json.dumps(config)))

    for key, name, device_class in ALERT_SENSORS:
        add("binary_sensor", key, {
            "name": name, "device_class": device_class, "state_topic": f"{prefix}/state",
            # "lock" in HA is on = unlocked - the opposite of "keys locked".
            "value_template": ("{{ 'OFF' if '%s' in value_json.alerts else 'ON' }}" if device_class == "lock"
                               else "{{ 'ON' if '%s' in value_json.alerts else 'OFF' }}") % key,
        })
    add("sensor", "phase", {"name": "Brewing", "state_topic": f"{prefix}/state",
                            "value_template": "{{ value_json.progress.phase }}", "icon": "mdi:coffee-maker"})
    add("sensor", "total", {"name": "Products total", "state_topic": f"{prefix}/counters",
                            "value_template": "{{ value_json.total }}", "state_class": "total_increasing",
                            "icon": "mdi:counter"})
    for product in products:
        add("sensor", f"count_{product['code']}", {
            "name": f"{product['name']} count", "state_topic": f"{prefix}/counters",
            "value_template": "{{ value_json.products['%s'] | default(0) }}" % product["code"],
            "state_class": "total_increasing", "icon": "mdi:coffee",
        })
        if brew_buttons:
            add("button", f"brew_{product['code']}", {
                "name": f"Brew {product['name']}", "command_topic": f"{prefix}/cmd",
                "payload_press": json.dumps({"action": "brew", "product": product["code"], "source": "ha"}),
                "icon": "mdi:coffee-to-go",
            })
    for name in ("cleaning", "filter_change", "decalc"):
        add("sensor", f"due_{name}", {
            "name": f"{name.replace('_', ' ').capitalize()} due", "state_topic": f"{prefix}/counters",
            "value_template": "{{ value_json.maintenance.percent.%s | default(None) }}" % name,
            "unit_of_measurement": "%", "icon": "mdi:wrench-clock",
        })
    return out
