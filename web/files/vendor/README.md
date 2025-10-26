Offline vendor files for CoffeePy web UI

Place the following files under this folder to enable offline use of the web UI:

- flatpickr/flatpickr.min.css
- flatpickr/flatpickr.min.js
- chartjs/chart.umd.min.js

This folder contains local copies of third-party libraries used by the CoffeePy web UI so the UI works without internet access.

If you need to update the vendor files, download the exact files listed below and place them in the directory structure shown.

Suggested manual download commands (run from the repository root):

curl (recommended):

```bash
mkdir -p web/files/vendor/flatpickr web/files/vendor/chartjs
curl -fsSL -o web/files/vendor/flatpickr/flatpickr.min.css https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css
curl -fsSL -o web/files/vendor/flatpickr/flatpickr.min.js  https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.js
curl -fsSL -o web/files/vendor/chartjs/chart.umd.min.js https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js
```

wget (alternative):

```bash
mkdir -p web/files/vendor/flatpickr web/files/vendor/chartjs
wget -q -O web/files/vendor/flatpickr/flatpickr.min.css https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css
wget -q -O web/files/vendor/flatpickr/flatpickr.min.js  https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.js
wget -q -O web/files/vendor/chartjs/chart.umd.min.js https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js
```

Notes:
- The project currently expects Chart.js v4.4.0 (the file above). You can update to a newer Chart.js release but keep the file name `chart.umd.min.js` and test charts afterwards.
- If you decide to use a different version of flatpickr ensure the API still supports the initialization used in `index.html` (time-only configuration).
- Keep the directory layout exactly as:

  web/files/vendor/flatpickr/flatpickr.min.css
  web/files/vendor/flatpickr/flatpickr.min.js
  web/files/vendor/chartjs/chart.umd.min.js

After updating the files, open `web/files/index.html` (via a local static server is recommended) to verify charts and the timepicker load correctly offline.
