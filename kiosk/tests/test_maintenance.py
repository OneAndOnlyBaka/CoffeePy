import json
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class MaintenanceOverlayTestCase(TestCase):
    def test_index_carries_the_overlay_and_translated_step_labels(self):
        response = self.client.get(reverse("kiosk:index"))
        self.assertContains(response, 'id="maintenance"')
        self.assertContains(response, 'data-status-url="/maintenance/status.json"')
        steps = json.loads(response.content.decode().split('id="maintenance-steps" type="application/json">')[1]
                           .split("</script>")[0])
        self.assertEqual(steps["restart"], "Restarting")

    def test_step_labels_follow_the_kiosk_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        self.assertContains(self.client.get(reverse("kiosk:index")), "Wird neu gestartet")

    def test_a_page_without_its_scripts_reloads_itself(self):
        # Inline, not a static file: it is what is left when the static files didn't load.
        html = self.client.get(reverse("kiosk:index")).content.decode()
        inline = [s for s in html.split("<script")[1:] if not s.lstrip().startswith("src=")]
        self.assertTrue(any("window.htmx" in s and "location.reload()" in s for s in inline))


class MaintenanceScriptTestCase(TestCase):
    """install/maintenance.sh, which install.sh and the update watcher source."""

    def run_steps(self, script):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                ["bash", "-c", f'MAINTENANCE_DIR="{directory}"; . install/maintenance.sh; {script}'],
                cwd=settings.BASE_DIR, check=True,
            )
            status = Path(directory, "status.json")
            return json.loads(status.read_text()) if status.exists() else None

    def test_step_writes_status_json(self):
        status = self.run_steps("maintenance_begin update; maintenance_step images 35")
        self.assertEqual((status["kind"], status["step"], status["percent"]), ("update", "images", 35))

    def test_end_removes_it(self):
        self.assertIsNone(self.run_steps("maintenance_begin install; maintenance_step app 30; maintenance_end"))

    def test_nothing_is_written_without_begin(self):
        self.assertIsNone(self.run_steps("maintenance_step app 30"))
