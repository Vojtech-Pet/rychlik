import json, os, re, subprocess, sys, time
from pathlib import Path

ADB = os.path.expanduser("~/Android/Sdk/platform-tools/adb")
PKG = "app.friendsend.friendsend"
SHOTS = Path(__file__).parent / "shots"
sys.path.insert(0, "/mnt/Data/Rychlik-app/src")
sys.path.insert(0, "/mnt/Data/Rychlik-app/tests")


def adb(*args, check=True, text=True, input=None):
    r = subprocess.run([ADB, *args], capture_output=True, text=text, input=input, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args} failed: {r.stderr}")
    return r.stdout


def shot(name):
    path = SHOTS / f"{name}.png"
    path.write_bytes(subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=60).stdout)
    return path


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))
    time.sleep(0.8)


def launch(clear=False):
    if clear:
        adb("shell", "pm", "clear", PKG)
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(5)


def type_text(s):
    # `input text` treats spaces specially; the payload is compact JSON so only quotes/braces need care
    adb("shell", "input", "text", "'" + s.replace("'", "").replace(" ", "%s") + "'")
    time.sleep(0.8)


def logcat_dump():
    return adb("logcat", "-d")
