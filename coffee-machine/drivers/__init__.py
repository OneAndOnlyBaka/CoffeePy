"""Machine drivers. A driver is a class taking the configured address with connect/disconnect,
`connected`, `lost` (an asyncio.Event), about/progress/alerts/counters/products and brew/lock - see
jura_ble.JuraBle. Another machine (the JURA's serial service port over a USB FTDI cable, or another
maker) is another module here, or another bridge altogether that speaks the same MQTT contract
(README.md)."""

DRIVERS = ("jura_ble",)


def load(name):
    if name == "jura_ble":
        from drivers import jura_ble  # noqa: PLC0415 - bleak only where it is used
        return jura_ble
    if name == "simulator":
        from drivers import simulator  # noqa: PLC0415
        return simulator
    raise ValueError(f"unknown driver {name!r}")


def machine_class(module):
    return getattr(module, "JuraBle", None) or getattr(module, "Simulator")
