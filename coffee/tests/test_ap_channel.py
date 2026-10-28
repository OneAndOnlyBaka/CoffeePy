import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = settings.BASE_DIR / "install" / "ap_channel.sh"


def scan_output(*networks):
    """What `iw dev wlan0 scan` prints, trimmed to the lines the script reads."""
    return "".join(f"BSS 00:11:22:33:44:{k:02x}(on wlan0)\n\tsignal: {signal:.2f} dBm\n"
                   f"\tSSID: net{k}\n\tDS Parameter set: channel {channel}\n"
                   for k, (channel, signal) in enumerate(networks))


@skipUnless(shutil.which("sh") and shutil.which("awk"), "needs a POSIX shell")
class AccessPointChannelTestCase(SimpleTestCase):
    """install/ap_channel.sh, with a fake iw (and ip, logger)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.scan = self.root / "scan.txt"
        fakes = {"iw": f'#!/bin/sh\n[ -s "{self.scan}" ] || exit 1\ncat "{self.scan}"\n',
                 "ip": "#!/bin/sh\nexit 0\n", "logger": "#!/bin/sh\nexit 0\n", "sleep": "#!/bin/sh\nexit 0\n"}
        for name, body in fakes.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.conf = self.root / "hostapd.conf"
        self.conf.write_text("interface=wlan0\nhw_mode=g\nchannel=7\nwmm_enabled=1\n")
        self.events = self.root / "events.jsonl"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "IW": str(bin_dir / "iw"),
                    "HOSTAPD_CONF": str(self.conf), "COFFEEPY_EVENTS": str(self.events)}

    def run_script(self, *networks):
        self.scan.write_text(scan_output(*networks) if networks else "")
        subprocess.run(["sh", str(SCRIPT)], env=self.env, check=True, capture_output=True)

    def pick(self, *networks):
        self.run_script(*networks)
        return self.conf.read_text().split("channel=")[1].split("\n")[0]

    def test_the_quietest_of_1_6_11(self):
        self.assertEqual(self.pick((1, -40), (6, -50), (11, -85)), "11")
        self.assertIn("wmm_enabled=1", self.conf.read_text())  # nothing else touched
        event = json.loads(self.events.read_text())
        self.assertEqual((event["action"], event["details"]), ("wifi.channel", "channel 7 -> 11, 3 networks around"))

    def test_one_loud_neighbour_outweighs_several_faint_ones(self):
        self.assertEqual(self.pick((1, -85), (1, -86), (1, -88), (6, -45), (11, -45)), "1")

    def test_overlapping_neighbours_count_too(self):
        # A router on 7 sits mostly on 6 and still partly on 11 - 1 is the only clean one.
        self.assertEqual(self.pick((7, -40), (11, -70)), "1")

    def test_nobody_around(self):
        self.assertEqual(self.pick((14, -90)), "1")  # out of reach of all three: the first

    def test_no_change_no_event(self):
        self.conf.write_text("channel=11\n")
        self.assertEqual(self.pick((1, -40), (6, -40)), "11")
        self.assertFalse(self.events.exists())

    def test_a_failed_scan_keeps_the_channel(self):
        self.assertEqual(self.pick(), "7")
        self.assertFalse(self.events.exists())

    def test_without_a_channel_line_nothing_happens(self):
        self.conf.write_text("interface=wlan0\n")
        self.run_script((1, -40))
        self.assertEqual(self.conf.read_text(), "interface=wlan0\n")
