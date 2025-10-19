import os
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash, generate_password_hash
from lib.clock.adjustment import datetime_setter
from lib.database.DatabaseConnector import Connector
from flask import jsonify
from datetime import datetime 
import time 
import configparser

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

# added endpoint: GET returns server time, POST sets it (requires privilege)
@app.route("/api/system_time", methods=["GET", "POST"])
@login_required
def api_system_time():
    if request.method == "GET":
        return jsonify({
            "timestamp": int(time.time()),
            "datetime": datetime.now().isoformat()
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

@app.route("/api/delete_user", methods=["POST"])
@login_required
def api_delete_user():
    data = request.get_json(silent=True)
    if not data or "uid" not in data:
        return jsonify({"error": "missing 'uid' field"}), 400

    uid = data["uid"]

    db = Connector()
    user_meta = db.GetUserMeta(uid)
    if user_meta is None:
        return jsonify({"error": "user not found"}), 404

    db.DeleteUser(uid)

    return jsonify({"status": "ok"})

@app.route("/api/database", methods=["GET"])
@login_required
def download_database():
    db_path = Connector().GetDatabasePath()
    if not os.path.exists(db_path):
        return jsonify({"error": "database file not found"}), 500

    with open(db_path, "rb") as f:
        data = f.read()

    response = app.response_class(
        response=data,
        status=200,
        mimetype="application/octet-stream",
    )
    response.headers.set("Content-Disposition", "attachment", filename="CoffeePy.db")
    return response

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