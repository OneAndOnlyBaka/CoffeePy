import os
import sys
import signal
import subprocess
import time

# Simple runner that starts the Flask webserver and the Tkinter app as separate subprocesses.
# Runs both scripts found in the repository root: webaccess/app.py and CoffeePy.py
# This keeps the Tkinter mainloop in its own process (required on many platforms).

PY = sys.executable
BASE = os.path.dirname(__file__)
WEB_PATH = os.path.join(BASE, "CoffeePyWeb.py")
GUI_PATH = os.path.join(BASE, "CoffeePy.py")

proc_web = None
proc_gui = None

def start_processes():
    global proc_web, proc_gui
    proc_web = subprocess.Popen([PY, WEB_PATH], cwd=BASE)
    proc_gui = subprocess.Popen([PY, GUI_PATH], cwd=BASE)
    print(f"Started webserver (pid={proc_web.pid}) and GUI (pid={proc_gui.pid})")

def terminate_children():
    for p in (proc_web, proc_gui):
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    # give them a moment, then force kill if still alive
    time.sleep(1.0)
    for p in (proc_web, proc_gui):
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

def signal_handler(signum, frame):
    print("Runner received signal, shutting down children...")
    terminate_children()
    sys.exit(0)

def monitor():
    try:
        while True:
            if proc_web and proc_web.poll() is not None:
                print(f"Webserver exited (code={proc_web.returncode}), shutting down runner.")
                break
            if proc_gui and proc_gui.poll() is not None:
                print(f"GUI exited (code={proc_gui.returncode}), shutting down runner.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        terminate_children()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    start_processes()
    monitor()