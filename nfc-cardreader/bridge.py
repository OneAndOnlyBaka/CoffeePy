import json
import logging
import os
import time

import paho.mqtt.client as mqtt
from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers
from smartcard.util import toHexString

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("acr122u-mqtt")

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "nfc/acr122u")
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "acr122u-bridge")
MQTT_TLS = os.environ.get("MQTT_TLS", "false").lower() == "true"
MQTT_QOS = int(os.environ.get("MQTT_QOS", "0"))
MQTT_RETAIN = os.environ.get("MQTT_RETAIN", "false").lower() == "true"

# ACS proprietary "Get Data" APDU that returns the card UID
GET_UID_APDU = [0xFF, 0xCA, 0x00, 0x00, 0x00]

# ACR122U "Direct Transmit" pseudo-APDU wrapping a PN532 InListPassiveTarget
# command (106 kbps type A). Response carries ATQA/SAK in addition to the UID.
DIRECT_TRANSMIT_INLIST_PASSIVE_TARGET = [0xFF, 0x00, 0x00, 0x00, 0x04, 0xD4, 0x4A, 0x01, 0x00]

# RID registered to the PC/SC Workgroup; precedes the "standard" and "card
# name" bytes in the historical bytes of every PC/SC contactless-card ATR
# (PC/SC Part 3 Supplement). Searching for it is more robust than fixed byte
# offsets since vendors prepend a variable number of proprietary TLV bytes.
PCSC_RID = (0xA0, 0x00, 0x00, 0x03, 0x06)

# Subset of the PC/SC Part 3 "card name" registry (2 bytes, big-endian) that
# follows the RID in the ATR historical bytes.
CARD_NAME_MAP = {
    0x0001: "MIFARE Classic 1K",
    0x0002: "MIFARE Classic 4K",
    0x0003: "MIFARE Ultralight",
    0x0004: "SLE55R_XXXX",
    0x0006: "SRI512",
    0x0007: "SRIX4K",
    0x0017: "PicoPass 2K",
    0x0019: "PicoPass 16K",
    0x0020: "I-Code UID",
    0x0021: "I-Code EPC",
    0x0027: "MIFARE Mini",
    0x0028: "MIFARE Plus SL1 2K",
    0x0029: "MIFARE Plus SL1 4K",
    0x002A: "MIFARE Plus SL2 2K",
    0x002B: "MIFARE Plus SL2 4K",
    0x002F: "FeliCa 212K",
    0x0030: "FeliCa 424K",
    0x0031: "MIFARE DESFire",
    0x0032: "MIFARE DESFire EV1 2K",
    0x0033: "MIFARE DESFire EV1 4K",
    0x0034: "MIFARE DESFire EV1 8K",
    0x003A: "my-d move NFC",
    0x003D: "MIFARE DESFire Light",
}

# NFC Forum URI RTD abbreviation codes for well-known URI records.
NDEF_URI_PREFIXES = {
    0x00: "",
    0x01: "http://www.",
    0x02: "https://www.",
    0x03: "http://",
    0x04: "https://",
    0x05: "tel:",
    0x06: "mailto:",
    0x07: "ftp://anonymous:anonymous@",
    0x08: "ftp://ftp.",
    0x09: "ftps://",
    0x0A: "sftp://",
    0x0B: "smb://",
    0x0C: "nfs://",
    0x0D: "ftp://",
    0x0E: "dav://",
    0x0F: "news:",
    0x10: "telnet://",
    0x11: "imap:",
    0x12: "rtsp://",
    0x13: "urn:",
    0x14: "pop:",
    0x15: "sip:",
    0x16: "sips:",
    0x17: "tftp:",
    0x18: "btspp://",
    0x19: "btl2cap://",
    0x1A: "btgoep://",
    0x1B: "tcpobex://",
    0x1C: "irdaobex://",
    0x1D: "file://",
    0x1E: "urn:epc:id:",
    0x1F: "urn:epc:tag:",
    0x20: "urn:epc:pat:",
    0x21: "urn:epc:raw:",
    0x22: "urn:epc:",
    0x23: "urn:nfc:",
}


def decode_atr_card_type(atr_bytes):
    atr = tuple(atr_bytes)
    for i in range(len(atr) - len(PCSC_RID) - 2):
        if atr[i:i + len(PCSC_RID)] == PCSC_RID:
            card_name = (atr[i + len(PCSC_RID) + 1] << 8) | atr[i + len(PCSC_RID) + 2]
            return CARD_NAME_MAP.get(card_name, f"Unknown (0x{card_name:04X})")
    return None


def get_atqa_sak(conn):
    try:
        data, sw1, sw2 = conn.transmit(DIRECT_TRANSMIT_INLIST_PASSIVE_TARGET)
    except CardConnectionException as exc:
        log.debug("Direct Transmit (ATQA/SAK) failed: %s", exc)
        return None
    if (sw1, sw2) != (0x90, 0x00):
        return None
    if len(data) < 7 or data[0] != 0xD5 or data[1] != 0x4B or data[2] < 1:
        return None
    return {
        "atqa": toHexString(data[4:6]).replace(" ", ""),
        "sak": f"{data[6]:02X}",
    }


def parse_ndef_message(data):
    records = []
    i = 0
    while i < len(data):
        flags = data[i]
        tnf = flags & 0x07
        short_record = bool(flags & 0x10)
        id_present = bool(flags & 0x08)
        i += 1

        type_len = data[i]
        i += 1

        if short_record:
            payload_len = data[i]
            i += 1
        else:
            payload_len = int.from_bytes(data[i:i + 4], "big")
            i += 4

        id_len = 0
        if id_present:
            id_len = data[i]
            i += 1

        rec_type = data[i:i + type_len]
        i += type_len
        if id_present:
            i += id_len

        payload = data[i:i + payload_len]
        i += payload_len

        record = {"type": rec_type.decode("ascii", errors="replace")}
        if tnf == 0x01 and rec_type == b"T" and payload:
            lang_len = payload[0] & 0x3F
            record["text"] = payload[1 + lang_len:].decode("utf-8", errors="replace")
        elif tnf == 0x01 and rec_type == b"U" and payload:
            prefix = NDEF_URI_PREFIXES.get(payload[0], "")
            record["uri"] = prefix + payload[1:].decode("utf-8", errors="replace")
        else:
            record["payload_hex"] = payload.hex()
        records.append(record)

        if flags & 0x40:  # ME (message end) bit
            break
    return records


def read_ndef(conn, start_page=4, max_pages=60):
    pages = []
    for page in range(start_page, start_page + max_pages):
        try:
            data, sw1, sw2 = conn.transmit([0xFF, 0xB0, 0x00, page, 0x04])
        except CardConnectionException:
            break
        if (sw1, sw2) != (0x90, 0x00):
            break
        pages.append(bytes(data))
    memory = b"".join(pages)

    idx = memory.find(b"\x03")
    if idx == -1:
        return None
    length = memory[idx + 1]
    if length == 0xFF:
        if idx + 4 > len(memory):
            return None
        length = int.from_bytes(memory[idx + 2:idx + 4], "big")
        ndef_start = idx + 4
    else:
        ndef_start = idx + 2

    ndef_bytes = memory[ndef_start:ndef_start + length]
    if not ndef_bytes:
        return None
    try:
        return parse_ndef_message(ndef_bytes)
    except IndexError:
        log.debug("Could not parse NDEF message (malformed/truncated)")
        return None


class MqttPublisher:
    def __init__(self):
        # Tracks the *reader's* last known state (set by main()'s presence loop, not by
        # MQTT connect/disconnect) so a broker reconnect republishes reality instead of
        # blindly claiming "online" again - see _on_connect.
        self._last_status = False
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        if MQTT_TLS:
            self.client.tls_set()
        self.client.will_set(f"{MQTT_TOPIC}/status", payload="offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self._connect()

    def _connect(self):
        while True:
            try:
                log.info("Connecting to MQTT broker %s:%s", MQTT_HOST, MQTT_PORT)
                self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
                self.client.loop_start()
                return
            except OSError as exc:
                log.warning("MQTT connect failed: %s - retrying in 5s", exc)
                time.sleep(5)

    def _on_connect(self, client, userdata, flags, rc):
        log.info("MQTT connected (rc=%s)", rc)
        self.publish_status(self._last_status)

    def _on_disconnect(self, client, userdata, rc):
        log.warning("MQTT disconnected (rc=%s)", rc)

    def publish_status(self, is_online):
        """The retained `<TOPIC>/status` message - driven by whether the PC/SC reader is
        actually present (see main()'s presence loop), not merely whether this process's
        MQTT connection happens to be up. The two used to be conflated: status was only
        ever set to "online" once, right after the MQTT connect, and the last-will
        "offline" only fired if *that* connection dropped - so unplugging the reader
        while MQTT stayed up left the retained topic stuck on "online" forever."""
        self._last_status = is_online
        self.client.publish(
            f"{MQTT_TOPIC}/status", "online" if is_online else "offline", qos=1, retain=True
        )

    def publish_event(
        self,
        event,
        uid=None,
        atr=None,
        reader=None,
        card_type=None,
        atqa=None,
        sak=None,
        ndef=None,
        dwell_time_ms=None,
    ):
        payload = {
            "event": event,
            "uid": uid,
            "uid_length": len(uid) // 2 if uid else None,
            "atr": atr,
            "card_type": card_type,
            "atqa": atqa,
            "sak": sak,
            "ndef": ndef,
            "dwell_time_ms": dwell_time_ms,
            "reader": reader,
            "timestamp": time.time(),
        }
        self.client.publish(MQTT_TOPIC, json.dumps(payload), qos=MQTT_QOS, retain=MQTT_RETAIN)
        log.info("Published: %s", payload)


class TagObserver(CardObserver):
    def __init__(self, publisher: MqttPublisher):
        self.publisher = publisher
        self._insert_times = {}

    @staticmethod
    def _card_key(card):
        return str(card.reader)

    def update(self, observable, actions):
        added, removed = actions
        for card in added:
            try:
                conn = card.createConnection()
                conn.connect()
                atr_bytes = conn.getATR()
                atr = toHexString(atr_bytes)
                card_type = decode_atr_card_type(atr_bytes)

                data, sw1, sw2 = conn.transmit(GET_UID_APDU)
                if (sw1, sw2) == (0x90, 0x00):
                    uid = toHexString(data).replace(" ", "")
                else:
                    uid = None
                    log.warning("GET UID failed sw=%02X%02X", sw1, sw2)

                atqa_sak = get_atqa_sak(conn) or {}
                ndef = read_ndef(conn)

                self._insert_times[self._card_key(card)] = time.time()

                self.publisher.publish_event(
                    "card_inserted",
                    uid=uid,
                    atr=atr,
                    reader=str(card.reader),
                    card_type=card_type,
                    atqa=atqa_sak.get("atqa"),
                    sak=atqa_sak.get("sak"),
                    ndef=ndef,
                )
            except (NoCardException, CardConnectionException) as exc:
                log.warning("Could not read card: %s", exc)
        for card in removed:
            insert_time = self._insert_times.pop(self._card_key(card), None)
            dwell_time_ms = int((time.time() - insert_time) * 1000) if insert_time else None
            self.publisher.publish_event(
                "card_removed", reader=str(card.reader), dwell_time_ms=dwell_time_ms
            )


READER_POLL_INTERVAL_SECONDS = 3
# The reader beeps on every card it detects unless told not to (ACR122U API 6.7). Not a
# setting it keeps: a power cut of the reader turns the beep back on, so it is sent at the
# start of every reader session.
READER_BEEP = os.environ.get("READER_BEEP", "0") == "1"
SET_BUZZER_ON_DETECTION = [0xFF, 0x00, 0x52, 0xFF if READER_BEEP else 0x00, 0x00]


def configure_reader(reader_name):
    """Sends the reader its settings through the PC/SC escape channel - possible without a
    card on it, which is why libccid's ifdDriverOptions 0x0001 is set in the Dockerfile.
    Linux's CCID driver takes the escape as SCARD_CTL_CODE(1), not the 3500 the ACS manual
    gives for Windows. Never fatal: a reader that won't take it still reads cards."""
    from smartcard.scard import (SCARD_CTL_CODE, SCARD_LEAVE_CARD, SCARD_PROTOCOL_UNDEFINED,
                                 SCARD_SCOPE_USER, SCARD_SHARE_DIRECT, SCardConnect, SCardControl,
                                 SCardDisconnect, SCardEstablishContext, SCardGetErrorMessage,
                                 SCardReleaseContext)
    hr, context = SCardEstablishContext(SCARD_SCOPE_USER)
    if hr != 0:
        log.warning("Reader settings not sent - no PC/SC context: %s", SCardGetErrorMessage(hr))
        return
    try:
        hr, handle, _ = SCardConnect(context, reader_name, SCARD_SHARE_DIRECT, SCARD_PROTOCOL_UNDEFINED)
        if hr != 0:
            log.warning("Reader settings not sent - cannot connect: %s", SCardGetErrorMessage(hr))
            return
        try:
            hr, response = SCardControl(handle, SCARD_CTL_CODE(1), SET_BUZZER_ON_DETECTION)
            if hr == 0 and list(response[:2]) == [0x90, 0x00]:
                log.info("Reader beep on card detection: %s", "on" if READER_BEEP else "off")
            else:
                log.warning("Reader did not take the beep setting: %s %s", SCardGetErrorMessage(hr),
                            toHexString(list(response)))
        finally:
            SCardDisconnect(handle, SCARD_LEAVE_CARD)
    finally:
        SCardReleaseContext(context)


def _list_readers():
    """readers() wrapped so a flaky pcscd right after a USB change (a brief
    SCardListReaders error) doesn't take the whole bridge down with it - treated the
    same as "no reader right now", which the caller already retries."""
    try:
        return readers()
    except Exception as exc:  # noqa: BLE001 - PCSC layer, exact exception types vary
        log.warning("Error listing PC/SC readers: %s", exc)
        return []


def wait_for_reader():
    while True:
        found = _list_readers()
        if found:
            log.info("Found reader(s): %s", found)
            return found
        log.info("No PC/SC reader found yet, retrying in %ss...", READER_POLL_INTERVAL_SECONDS)
        time.sleep(READER_POLL_INTERVAL_SECONDS)


def run_session(publisher):
    """Covers one reader-attached session: builds a fresh CardMonitor/TagObserver and
    polls reader presence *from this thread* until the reader disappears, then tears
    down and returns so main() can wait for it to come back.

    Deliberately doesn't just trust CardMonitor's own background thread to notice a
    reader going away - pyscard's underlying SCardGetStatusChange call can end up wedged
    after a raw USB unplug depending on version/platform, and an uncaught exception in
    that background thread would otherwise just silently kill it (Python doesn't crash
    the process for an exception in a non-main thread), leaving the bridge looking
    "up" forever with no way to ever see another card again. Polling readers() directly
    from here, each iteration, is an independent liveness check that doesn't depend on
    that thread staying healthy."""
    for reader in _list_readers():
        configure_reader(str(reader))
    observer = TagObserver(publisher)
    monitor = CardMonitor()
    monitor.addObserver(observer)
    publisher.publish_status(True)
    log.info("Listening for NFC tags, publishing to topic '%s' ...", MQTT_TOPIC)
    try:
        while _list_readers():
            time.sleep(READER_POLL_INTERVAL_SECONDS)
        log.warning("Reader disappeared - waiting for it to come back")
    finally:
        monitor.deleteObserver(observer)
        publisher.publish_status(False)


def main():
    publisher = MqttPublisher()
    try:
        while True:
            wait_for_reader()
            run_session(publisher)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
