import fcntl
import glob
import os
import signal
import subprocess
import threading
import time
from flask import Flask, jsonify, render_template

app = Flask(__name__)

BASE_DIR = "/app"
HLS_DIR = os.path.join(BASE_DIR, "hls")
STATE_DIR = os.path.join(BASE_DIR, "state")

LAST_SEEN_FILE = os.path.join(STATE_DIR, "last_seen")
PID_FILE = os.path.join(STATE_DIR, "ffmpeg.pid")
LOCK_FILE = os.path.join(STATE_DIR, "lock")

CAMERA_DEVICE = os.environ.get(
    "CAMERA_DEVICE",
    "/dev/video0"
)
CAMERA_SIZE = os.environ.get("CAMERA_SIZE", "640x480")
CAMERA_FPS = os.environ.get("CAMERA_FPS", "10")
HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "300"))
HLS_TIME = max(1.0, float(os.environ.get("HLS_TIME", "2")))
HLS_LIST_SIZE = max(3, int(os.environ.get("HLS_LIST_SIZE", "6")))
STARTUP_SEGMENTS = max(1, int(os.environ.get("STARTUP_SEGMENTS", "2")))
DEBUG_LOG = os.environ.get("DEBUG_LOG", "0") == "1"

os.makedirs(HLS_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)


def lock_fd():
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def unlock_fd(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_pid():
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def write_pid(pid):
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def is_pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_hls():
    for path in glob.glob(os.path.join(HLS_DIR, "*.ts")):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    for path in glob.glob(os.path.join(HLS_DIR, "*.m3u8")):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def touch_last_seen():
    with open(LAST_SEEN_FILE, "w") as f:
        f.write(str(int(time.time())))


def get_last_seen():
    if not os.path.exists(LAST_SEEN_FILE):
        return None
    try:
        with open(LAST_SEEN_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def ffmpeg_command():
    try:
        fps = max(1, int(float(CAMERA_FPS)))
    except ValueError:
        fps = 10

    gop = max(1, int(round(fps * HLS_TIME)))

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-fflags", "nobuffer",
        "-f", "video4linux2",
        "-input_format", "mjpeg",
        "-video_size", CAMERA_SIZE,
        "-framerate", CAMERA_FPS,
        "-i", CAMERA_DEVICE,
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-f", "hls",
        "-hls_time", str(HLS_TIME),
        "-hls_list_size", str(HLS_LIST_SIZE),
        "-hls_delete_threshold", "4",
        "-hls_flags", "delete_segments+independent_segments+program_date_time",
        "-hls_segment_filename", os.path.join(HLS_DIR, "segment_%03d.ts"),
        os.path.join(HLS_DIR, "index.m3u8"),
    ]


def get_playlist_segment_count():
    playlist_path = os.path.join(HLS_DIR, "index.m3u8")
    if not os.path.exists(playlist_path):
        return 0

    try:
        with open(playlist_path, "r") as f:
            return sum(1 for line in f if line.startswith("#EXTINF:"))
    except Exception:
        return 0


def start_ffmpeg_if_needed():
    fd = lock_fd()
    try:
        pid = read_pid()
        if pid and is_pid_alive(pid):
            touch_last_seen()
            return {"started": False, "pid": pid, "message": "already running"}

        cleanup_hls()
        if DEBUG_LOG:
            log_path = os.path.join(STATE_DIR, "ffmpeg.log")
            ffmpeg_stdout = open(log_path, "ab", buffering=0)
        else:
            ffmpeg_stdout = subprocess.DEVNULL

        proc = subprocess.Popen(
            ffmpeg_command(),
            stdout=ffmpeg_stdout,
            stderr=ffmpeg_stdout,
            preexec_fn=os.setsid,
        )
        write_pid(proc.pid)
        touch_last_seen()
        return {"started": True, "pid": proc.pid, "message": "started"}
    finally:
        unlock_fd(fd)


def stop_ffmpeg():
    fd = lock_fd()
    try:
        pid = read_pid()
        if pid and is_pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

            # 少し待つ
            for _ in range(10):
                if not is_pid_alive(pid):
                    break
                time.sleep(0.3)

            # まだ生きていたら強制終了
            if is_pid_alive(pid):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        remove_pid()
        cleanup_hls()
        return True
    finally:
        unlock_fd(fd)


def get_status():
    pid = read_pid()
    alive = is_pid_alive(pid)
    last_seen = get_last_seen()
    now = int(time.time())
    idle_seconds = None if last_seen is None else now - last_seen
    playlist_exists = os.path.exists(os.path.join(HLS_DIR, "index.m3u8"))
    segment_count = get_playlist_segment_count()

    return {
        "running": alive,
        "pid": pid if alive else None,
        "playlist_exists": playlist_exists,
        "segment_count": segment_count,
        "last_seen": last_seen,
        "idle_seconds": idle_seconds,
        "timeout_seconds": HEARTBEAT_TIMEOUT,
        "camera_device": CAMERA_DEVICE,
        "hls_time": HLS_TIME,
        "startup_segments": STARTUP_SEGMENTS,
    }


@app.route("/")
def root():
    return render_template("camera.html")


@app.route("/camera")
def camera():
    return render_template("camera.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    result = start_ffmpeg_if_needed()
    return jsonify(result)


@app.route("/api/ping", methods=["POST"])
def api_ping():
    touch_last_seen()
    return jsonify({"ok": True, "ts": int(time.time())})


@app.route("/api/status")
def api_status():
    return jsonify(get_status())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    stop_ffmpeg()
    return jsonify({"ok": True})


def watchdog_loop():
    while True:
        try:
            st = get_status()
            if st["running"] and st["idle_seconds"] is not None:
                if st["idle_seconds"] >= HEARTBEAT_TIMEOUT:
                    stop_ffmpeg()
        except Exception:
            pass
        time.sleep(10)


watchdog_started = False

@app.before_request
def ensure_watchdog():
    global watchdog_started
    if not watchdog_started:
        t = threading.Thread(target=watchdog_loop, daemon=True)
        t.start()
        watchdog_started = True


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
