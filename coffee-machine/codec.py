"""What a JURA's BlueFrog (Smart Connect dongle or built-in) sends, decoded - frames already run
through py_jura.encoder.encdec, so this module needs neither bleak nor py-jura and the app's tests
can import it. Every layout here was measured on an E6 (EF532M V02.03, BlueFrog V05.08F) on
2026-11-06; py-jura's and Jutta-Proto's docs disagree with it in places, the device wins."""

# Product progress, byte 1. What py-jura's README lists (0x24 "ready" and others) never showed up.
STATE_IDLE = 0x00
STATE_HEATING = 0x21  # before the grinder: heating up, byte 4 of byte 5 steps done
STATE_STARTING = 0x39  # the recipe is known (bytes 3-6, 11)
STATE_DISPENSING = 0x3C  # byte 5 of byte 6 steps (5 ml each) done, byte 16 percent
STATE_DONE = 0x3E
# Also listed by py-jura as active; not seen on the E6, kept so another model isn't taken for idle.
ACTIVE_STATES = {0x0E, STATE_HEATING, 0x24, 0x25, STATE_STARTING, STATE_DISPENSING, STATE_DONE}

PHASES = {
    STATE_HEATING: "heating",
    STATE_STARTING: "starting",
    STATE_DISPENSING: "dispensing",
    STATE_DONE: "done",
}

# Statistics command readback: the E6 says "data ready" with 0x0e in byte 0 - py-jura 0.2.1 waits
# for the key there instead and times out on this firmware.
STATS_READY = 0x0E

# The unused slots of the product counters.
NO_COUNTER = 0xFFFF


def progress(frame: bytes) -> dict:
    """{"state", "phase", "product", ...} from a decoded progress frame. The recipe (strength,
    water_ml, temperature) is only there once the machine starts (0x39/0x3C) - also for a product
    chosen at the machine, which is how the water used is known without a flow meter."""
    state = frame[1] if len(frame) > 1 else STATE_IDLE
    product = frame[2] if len(frame) > 2 else 0
    result = {"state": state, "phase": PHASES.get(state, "idle" if state not in ACTIVE_STATES else "busy"),
              "product": product if state in ACTIVE_STATES else 0}
    if state in (STATE_STARTING, STATE_DISPENSING) and len(frame) >= 17:
        result.update(strength=frame[4], water_ml=frame[6] * 5, temperature=frame[11] & 0x0F)
        if state == STATE_DISPENSING and frame[16] <= 100:
            result["percent"] = frame[16]
    elif state == STATE_HEATING and len(frame) >= 6 and frame[5]:
        result["percent"] = min(100, frame[4] * 100 // frame[5])
    return result


def is_active(state: int) -> bool:
    return state in ACTIVE_STATES and state != STATE_DONE


def alert_bits(frame: bytes) -> list[int]:
    """The set bits of a decoded machine-status frame: bit i is byte 1 + i // 8, MSB first."""
    return [i for i in range(8 * (len(frame) - 1)) if frame[1 + i // 8] >> (7 - i % 8) & 1]


def counters(frame: bytes) -> dict:
    """{"total", "products": {code: count}} from the PRODUCT_COUNTERS statistics: 3-byte big-endian
    slots, slot 0 the total, slot n product code n. Every slot that counts is kept, also for codes
    the machine definition doesn't list (the E6's hot water and "2 coffees" aren't in py-jura's)."""
    slots = [int.from_bytes(frame[i:i + 3], "big") for i in range(0, len(frame) - 2, 3)]
    products = {code: n for code, n in enumerate(slots) if code and n != NO_COUNTER}
    # Trailing zeros are slots no product uses - only keep codes that ever counted, or that are
    # plausible products (below 0x40, py-jura's highest code is 0x3D).
    products = {code: n for code, n in products.items() if n or code < 0x40}
    return {"total": slots[0] if slots else 0, "products": products}


def maintenance(counter_frame: bytes, percent_frame: bytes, counter_names, percent_names) -> dict:
    """Maintenance counters (2-byte slots) and how far each is due (1-byte percent slots)."""
    counts = {name: int.from_bytes(counter_frame[i * 2:i * 2 + 2], "big")
              for i, name in enumerate(counter_names) if len(counter_frame) >= i * 2 + 2}
    percent = {name: percent_frame[i] for i, name in enumerate(percent_names)
               if len(percent_frame) > i and percent_frame[i] <= 100}
    return {"counters": counts, "percent": percent}


def advertisement(data: bytes) -> tuple[int, int] | None:
    """(key, article number) from the manufacturer data 0x00AB - None if it is missing or short,
    which happens: some advertisements arrive without it, the next one carries it again. The key is
    not fixed (it changed between two sessions of the same dongle), so it is read at every connect."""
    if not data or len(data) < 6:
        return None
    return data[0], int.from_bytes(data[4:6], "little")
