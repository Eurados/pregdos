"""The supported launcher, real Gunicorn workers, and DICOM-sized HTTP transfers."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from pregdos.server import Application, Worker, options


SCRIPT = '''
import hashlib
import os
from pathlib import Path
from flask import request, session
from pregdos import server, webserver

class Application(server.Application):
    def load_config(self):
        super().load_config()
        self.cfg.set("when_ready", lambda arbiter: Path(os.environ["READY_FILE"]).write_text(
            str(arbiter.LISTENERS[0].getsockname()[1])))

server.Application = Application

@webserver.app.post("/__test__/upload")
def upload():
    size = 0
    digest = hashlib.sha256()
    files = request.files.getlist("files")
    for item in files:
        while block := item.stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return {"size": size, "files": len(files), "sha256": digest.hexdigest()}

@webserver.app.get("/__test__/session")
def session_count():
    session["count"] = session.get("count", 0) + 1
    return {"count": session["count"], "pid": os.getpid()}

@webserver.app.get("/__test__/stream")
def stream():
    return webserver.Response((b"x" * (1024 * 1024) for _ in range(128)))

webserver.main()
'''


@pytest.fixture
def running_server(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'[paths]\nwork_dir = "{tmp_path / "studies"}"\n[network]\nupdate_check = false\n')
    ready = tmp_path / "ready"
    env = os.environ.copy()
    env.update(PREGDOS_CONFIG=str(config), STATE_DIRECTORY=str(tmp_path / "state"), READY_FILE=str(ready))
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", SCRIPT, "--host", "127.0.0.1", "--port", "0", "--workers", "2"],
            cwd=Path(__file__).resolve().parent.parent, env=env, stdout=log, stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            assert ready.exists(), log_path.read_text()
            yield int(ready.read_text()), process
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                pytest.fail("Gunicorn did not shut down: " + log_path.read_text())


def test_launch_options_cannot_be_overridden_by_ambient_gunicorn_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gunicorn.conf.py").write_text('bind = "0.0.0.0:9999"\n')
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--bind 0.0.0.0:8888 --workers 9")
    app = Application(lambda environ, start_response: [], options("127.0.0.1", 5000))
    assert app.cfg.bind == ["127.0.0.1:5000"]
    assert app.cfg.workers == 1
    assert app.cfg.threads == 8
    assert app.cfg.worker_class is Worker


def test_ipv6_bind():
    app = Application(lambda environ, start_response: [], options("::1", 5000))
    assert app.cfg.address == [("::1", 5000)]


def test_large_multipart_study_and_many_instances(running_server):
    port, _ = running_server
    # A 128 MiB file and 1,001 small instances exercise both upload bytes and the
    # Flask multipart-count default that would otherwise reject large CT folders.
    small = b"".join(
        f'--study\r\nContent-Disposition: form-data; name="files"; filename="{i}.dcm"\r\n\r\nx\r\n'.encode()
        for i in range(1001)
    )
    prefix = b'--study\r\nContent-Disposition: form-data; name="files"; filename="large.dcm"\r\n\r\n'
    suffix = b"\r\n--study--\r\n"
    block = b"x" * (1024 * 1024)
    expected = hashlib.sha256(b"x" * 1001)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.putrequest("POST", "/__test__/upload")
        connection.putheader("Content-Type", "multipart/form-data; boundary=study")
        connection.putheader("Content-Length", str(len(small) + len(prefix) + 128 * len(block) + len(suffix)))
        connection.endheaders()
        connection.send(small + prefix)
        for _ in range(128):
            connection.send(block)
            expected.update(block)
        connection.send(suffix)
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {
            "size": 128 * len(block) + 1001, "files": 1002, "sha256": expected.hexdigest(),
        }
    finally:
        connection.close()


def test_large_streamed_download(running_server):
    port, _ = running_server
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.request("GET", "/__test__/stream")
        response = connection.getresponse()
        assert response.status == 200
        size = 0
        while block := response.read(1024 * 1024):
            assert block == b"x" * len(block)
            size += len(block)
        assert size == 128 * 1024 * 1024
    finally:
        connection.close()


def test_sessions_survive_worker_replacement(running_server):
    port, master = running_server
    cookie = ""

    def visit(expected_count):
        nonlocal cookie
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("GET", "/__test__/session", headers={"Cookie": cookie, "Connection": "close"})
            response = connection.getresponse()
            assert response.status == 200
            cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            body = json.loads(response.read())
            assert body["count"] == expected_count
            return body["pid"]
        finally:
            connection.close()

    first_pid = visit(1)
    os.kill(first_pid, signal.SIGKILL)
    pids = set()
    for count in range(2, 32):
        pids.add(visit(count))
        if len(pids) == 2:
            break
        time.sleep(0.05)
    assert len(pids) == 2, "both replacement and surviving worker should serve the same session"
    assert first_pid not in pids
    assert master.poll() is None
