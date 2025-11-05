import os
import shutil
import tempfile
import zipfile
import hashlib
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from lib.clock.adjustment import datetime_setter
from lib.database.DatabaseConnector import Connector
from flask import jsonify
from datetime import datetime,timedelta
import time 
import configparser
from pathlib import Path
import sys
import threading
import signal
import subprocess

# /home/tom/CoffeePy/webaccess/app.py
# Simple Flask webserver with a single static-user login


# tell Flask to look for templates in the "files" directory next to this module
app = Flask(__name__, template_folder="web/files")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")  # change in production

# Static credentials (single user). Password stored as a hash.
STATIC_USERNAME = "admin"
# Hash for password "secret" (generated once). You can replace with generate_password_hash("yourpass")
STATIC_PASSWORD_HASH = generate_password_hash("secret")

# Simple login-required decorator
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("username") != STATIC_USERNAME:
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# templates are served from files/login.html, files/index.html, files/protected.html

@app.route("/")
def index():
    user = session.get("username")
    if user == STATIC_USERNAME:
        return render_template("index.html", user=user)
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == STATIC_USERNAME and check_password_hash(STATIC_PASSWORD_HASH, password):
            session["username"] = username
            next_page = request.args.get("next") or url_for("index")
            return redirect(next_page)
        error = "Invalid username or password"
    return render_template("login.html", error=error, username=request.form.get("username", ""))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/rebooting")
def rebooting_html():
    return render_template("rebooting.html")

# added endpoint: GET returns server time, POST sets it (requires privilege)
@app.route("/api/system_time", methods=["GET", "POST"])
@login_required
def api_system_time():
    if request.method == "GET":
        return jsonify({
            "timestamp": int(time.time()),
            "datetime": datetime.now().astimezone().isoformat()
        })

    # POST
    data = request.get_json(silent=True)
    if not data or "datetime" not in data:
        return jsonify({"error": "missing 'datetime' field"}), 400

    dt_str = data["datetime"]
    try:
        # Accept ISO-like input from <input type="datetime-local"> (e.g. "2025-10-19T12:34")
        # datetime.fromisoformat handles this format
        new_dt = datetime.fromisoformat(dt_str)
    except Exception as e:
        return jsonify({"error": "invalid datetime format", "detail": str(e)}), 400

    # Ensure datetime is in the server's local timezone:
    # - treat naive datetimes as local time
    # - convert timezone-aware datetimes to local timezone if different
    local_tz = datetime.now().astimezone().tzinfo
    if new_dt.tzinfo is None:
        # interpret naive datetime as local time
        new_dt = new_dt.replace(tzinfo=local_tz)
    else:
        try:
            new_dt = new_dt.astimezone(local_tz)
        except Exception:
            # if conversion fails, leave the parsed datetime as-is
            pass

    try:
        datetime_setter.set_system_datetime(new_dt)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": "failed to set system time", "detail": str(e)}), 500
        
@app.route("/api/user_list", methods=["GET"])
@login_required
def api_user_list():
    db = Connector()
    return jsonify(db.GetUserList())


@app.route("/api/database", methods=["GET"])
@login_required
def api_database():
    """Return the underlying SQLite database file as a downloadable attachment.

    Uses the DatabaseConnector to locate the file so the endpoint works even if
    the project has been started from a different working directory.
    """
    db = Connector()
    db_path = db.GetDatabasePath()
    if not os.path.exists(db_path):
        return jsonify({"error": "database file not found"}), 404
    try:
        # Use send_file to return the file as an attachment. Flask will set
        # Content-Disposition for us. Return as generic octet-stream so
        # browsers always download the file rather than attempt to open it.
        return send_file(db_path, mimetype='application/octet-stream', as_attachment=True, download_name=os.path.basename(db_path))
    except TypeError:
        # Fallback for older Flask versions that use 'attachment_filename'
        try:
            return send_file(db_path, mimetype='application/octet-stream', as_attachment=True, attachment_filename=os.path.basename(db_path))
        except Exception as e:
            return jsonify({"error": "failed to send database", "detail": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "failed to send database", "detail": str(e)}), 500


def _safe_copy_tree(src_dir, dest_dir):
    """Recursively copy files from src_dir to dest_dir, overwriting existing files.
    Preserves file permissions. Raises on any low-level IO error so callers can roll back."""
    src = Path(src_dir)
    dest = Path(dest_dir)
    if not src.exists():
        raise FileNotFoundError(str(src))
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = dest.joinpath(rel) if rel != '.' else dest
        target_root.mkdir(parents=True, exist_ok=True)
        for fname in files:
            s = Path(root) / fname
            d = target_root / fname
            # use atomic replace: write to temp then replace
            with tempfile.NamedTemporaryFile(dir=str(target_root), delete=False) as tf:
                tf_name = Path(tf.name)
                tf.close()
            shutil.copy2(s, tf_name)
            os.replace(str(tf_name), str(d))


def _make_backup(paths, backup_dir):
    """Create a zip backup of the list of paths (files or directories) into backup_dir.
    Returns path to backup zip file."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    bpath = backup_dir / f"backup_{int(time.time())}.zip"
    with zipfile.ZipFile(bpath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            ppath = Path(p)
            if not ppath.exists():
                continue
            if ppath.is_file():
                zf.write(p, arcname=str(ppath.relative_to(Path.cwd())))
            else:
                for root, dirs, files in os.walk(ppath):
                    for f in files:
                        full = Path(root) / f
                        try:
                            arc = full.relative_to(Path.cwd())
                        except Exception:
                            arc = full
                        zf.write(full, arcname=str(arc))
    return str(bpath)


@app.route('/api/upload_update', methods=['POST'])
@login_required
def api_upload_update():
    """Upload a zip file containing an update. The zip should contain files to replace
    relative to the project root. Optional top-level file 'VERSION' is read for info.

    Workflow:
    - accept multipart file field 'file'
    - validate it's a zip
    - extract to a temp dir
    - optionally read VERSION or version.txt
    - create a backup zip of current files that will be overwritten
    - copy extracted files into project root atomically
    - on error, attempt to restore from backup
    """
    if 'file' not in request.files:
        return jsonify({'error': 'missing file field'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'empty filename'}), 400

    # basic filename sanitization
    filename = os.path.basename(f.filename)
    # require .zip
    if not filename.lower().endswith('.zip'):
        return jsonify({'error': 'only .zip files are supported'}), 400

    tmpdir = tempfile.mkdtemp(prefix='coffeepy_update_')
    try:
        zpath = os.path.join(tmpdir, filename)
        f.save(zpath)
        # make sure it's a valid zip
        try:
            with zipfile.ZipFile(zpath, 'r') as zf:
                zf.testzip()  # returns None if OK or name of first bad file
        except zipfile.BadZipFile:
            return jsonify({'error': 'uploaded file is not a valid zip'}), 400

        # extract to extract_dir
        extract_dir = os.path.join(tmpdir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zpath, 'r') as zf:
            zf.extractall(extract_dir)

        # optional version info
        version = None
        for candidate in ('VERSION', 'version', 'version.txt', 'VERSION.txt'):
            p = os.path.join(extract_dir, candidate)
            if os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as fh:
                        version = fh.read().strip()
                except Exception:
                    pass
                break

        # build list of files to overwrite (relative paths inside zip)
        files_to_overwrite = []
        for root, dirs, files in os.walk(extract_dir):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, extract_dir)
                # we will place rel into project root
                files_to_overwrite.append(rel)

        # create backup of existing files that will be overwritten
        project_root = os.getcwd()
        existing_paths = [os.path.join(project_root, p) for p in files_to_overwrite if os.path.exists(os.path.join(project_root, p))]
        backup_dir = os.path.join(project_root, 'tmp_update_backups')
        backup_zip = None
        if existing_paths:
            try:
                backup_zip = _make_backup(existing_paths, backup_dir)
            except Exception as e:
                return jsonify({'error': 'failed to create backup', 'detail': str(e)}), 500

        # attempt to copy files into place
        try:
            _safe_copy_tree(extract_dir, project_root)
        except Exception as e:
            # try rollback if we have a backup
            if backup_zip and os.path.exists(backup_zip):
                try:
                    with zipfile.ZipFile(backup_zip, 'r') as zf:
                        zf.extractall(project_root)
                except Exception as e2:
                    return jsonify({'error': 'update failed and rollback failed', 'detail': f'{e} | rollback: {e2}'}), 500
            return jsonify({'error': 'failed to apply update', 'detail': str(e)}), 500

        # success
        resp = {'status': 'ok'}
        if version:
            resp['version'] = version
        resp['backup'] = os.path.basename(backup_zip) if backup_zip else None

        # Schedule a system reboot after a short delay (3s) and instruct the
        # client to navigate to the rebooting page. We perform the reboot in a
        # background thread so the HTTP response can be returned immediately.
        def _delayed_reboot(delay_seconds=3):
            try:
                time.sleep(delay_seconds)
                # Try systemctl reboot first, fall back to shutdown -r now
                # Use subprocess.call to avoid shell quoting issues
                try:
                    subprocess.call(['systemctl', 'reboot'])
                except Exception:
                    try:
                        subprocess.call(['shutdown', '-r', 'now'])
                    except Exception:
                        # As a last resort, try reboot command
                        try:
                            subprocess.call(['reboot'])
                        except Exception:
                            # If all reboot attempts failed, log to stderr
                            print('Failed to reboot system after update', file=sys.stderr)
            except Exception as e:
                print(f'Error in delayed reboot thread: {e}', file=sys.stderr)

        t = threading.Thread(target=_delayed_reboot, args=(3,), daemon=True)
        t.start()

        # return a response that tells the client to redirect to the rebooting page
        # The web UI should navigate the user to /rebooting where an informational
        # page will show and then redirect to the login page after bootup.
        resp['reboot'] = True
        resp['reboot_path'] = url_for('rebooting')
        return jsonify(resp)
    finally:
        # cleanup tmpdir
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

@app.route("/api/user", methods=["PUT"])
@login_required
def api_user_update():
    data = request.get_json(silent=True)
    if not data or "uid" not in data:
        return jsonify({"error": "missing 'uid' field"}), 400

    uid = data["uid"]
    alt_uid = data.get("alt_uid")
    nick_name = data.get("nick_name")
    favourite_coffee = data.get("favourite_coffee")

    db = Connector()
    user_meta = db.GetUserMeta(uid)
    if user_meta is None:
        return jsonify({"error": "user not found"}), 404

    if alt_uid is not None:
        db.UpdateUserAltUID(uid, alt_uid)
    if nick_name is not None:
        user_meta['nick_name'] = nick_name
    if favourite_coffee is not None:
        user_meta['favourite_coffee'] = favourite_coffee

    if nick_name is not None or favourite_coffee is not None:
        db.UpdateUserMeta(uid, user_meta['nick_name'], user_meta['favourite_coffee'])

    return jsonify({"status": "ok"})

@app.route("/api/coffee_sorts", methods=["GET"])
@login_required
def api_coffee_sorts():
    db = Connector()
    return jsonify(db.GetCoffeeSorts())

@app.route("/api/coffee_sort", methods=["PUT"])
@login_required
def api_coffee_sort_update():
    data = request.get_json(silent=True)
    if not data or "name" not in data:
        return jsonify({"error": "missing 'name' field"}), 400

    name = data["name"]
    price = data.get("price")
    strokes = data.get("strokes")

    if price is None or strokes is None:
        return jsonify({"error": "missing 'price' or 'strokes' field"}), 400

    db = Connector()
    db.UpdateCoffeeSort(name, price, strokes)

    return jsonify({"status": "ok"})

@app.route("/api/order_list", methods=["GET"])
def api_order_list():
    db = Connector()

    payload = request.get_json(silent=True) or {}
    start = payload.get("start")
    end = payload.get("end")
    span = None
    if start is not None and end is not None:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            span = end_dt - start_dt
        except Exception as e:
            return jsonify({"error": "invalid 'start' or 'end' datetime format", "detail": str(e)}), 400

    orders = db.GetAllOrders(timespan=span)
    return jsonify(orders)


@app.route("/api/payment_list", methods=["GET"])
def api_payment_list():
    db = Connector()
    payload = request.get_json(silent=True) or {}
    start = payload.get("start")
    end = payload.get("end")
    span = None
    if start is not None and end is not None:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            span = end_dt - start_dt
        except Exception as e:
            return jsonify({"error": "invalid 'start' or 'end' datetime format", "detail": str(e)}), 400

    payments = db.GetAllPayments(timespan=span)
    return jsonify(payments)
@app.route("/api/deposit_pillory", methods=["GET"])
@login_required
def get_deposit_pillory():
    db = Connector()
    pillory_data = db.GetPillorySortedByDecreasing()
    return jsonify(pillory_data)


# Serve local vendor static files (flatpickr, Chart.js, etc.)
# The files are expected under the templates folder at web/files/vendor/*
@app.route('/vendor/<path:filename>')
def vendor_static(filename):
    vendor_dir = os.path.join(app.template_folder, 'vendor')
    # send_from_directory will return 404 automatically if file is missing
    return send_from_directory(vendor_dir, filename)


@app.route('/rebooting')
def rebooting():
    # Serve a simple static template that informs the user the system is
    # rebooting and will redirect back to the login page after the expected
    # boot time.
    return render_template('rebooting.html')


if __name__ == "__main__":
    if os.path.exists('CoffeePy.ini'):
        config = configparser.ConfigParser()
        config.read('CoffeePy.ini')
        if 'web' in config.sections():
            try:
                admin_password = config['web']['admin_password']
                STATIC_PASSWORD_HASH = generate_password_hash(admin_password)
            except:
                pass
            try:
                port = int(config['web'].get('port', 8000))
            except:
                port = 8000

    # For development only. In production use a proper WSGI server.
    app.run(host="0.0.0.0", port=port, debug=False)