"""A stand-in for ecmwf.datastores.Client: the same submit/get_remote/status/download/delete calls."""

from __future__ import annotations

import types

from .gridutil import make_area_grib


class FakeNotFound(Exception):
    def __init__(self):
        super().__init__("404 Client Error: Not Found for url: https://cds.example/api/retrieve/v1/jobs/x")
        self.response = types.SimpleNamespace(status_code=404)


class FakeRemote:
    def __init__(self, client, request_id):
        self._client, self.request_id = client, request_id

    @property
    def status(self):
        return self._client.jobs[self.request_id]["status"]

    def download(self, target):
        self._client.download_calls += 1
        if self._client.fail_downloads:
            self._client.fail_downloads -= 1
            open(target, "wb").write(b"GRIB-half")       # leaves a partial file behind, like a dropped connection
            raise ConnectionError("connection reset")
        if self._client.kill_downloads:
            self._client.kill_downloads -= 1
            open(target, "wb").write(b"GRIB-half")
            raise KeyboardInterrupt            # not an Exception: the code's own error handling is bypassed
        request = self._client.jobs[self.request_id]["request"]
        make_area_grib(target, request["area"], request["date"].replace("/to/", "/"), request["time"])
        return target

    def delete(self):
        self._client.deleted.append(self.request_id)


class FakeClient:
    def __init__(self, queue_limit=None):
        self.jobs: dict = {}
        self.queue_limit = queue_limit
        self.network_down = False
        self.fail_downloads = 0
        self.kill_downloads = 0          # a hard kill in the middle of a download: no cleanup code can run
        self.download_calls = 0
        self.deleted: list = []
        self.submitted: list = []

    def _active(self):
        return sum(1 for j in self.jobs.values() if j["status"] in ("accepted", "running"))

    def submit(self, collection_id, request):
        assert collection_id == "reanalysis-era5-complete"
        if self.queue_limit is not None and self._active() >= self.queue_limit:
            raise RuntimeError("403 Forbidden: too many queued requests")
        request_id = f"job-{len(self.jobs) + 1}"
        self.jobs[request_id] = {"request": request, "status": "accepted"}
        self.submitted.append(request)
        return FakeRemote(self, request_id)

    def get_remote(self, request_id):
        if self.network_down:
            raise ConnectionError("network is unreachable")
        if request_id not in self.jobs:
            raise FakeNotFound()
        return FakeRemote(self, request_id)

    # --- helpers for the tests ---
    def finish(self, status="successful", only=None):
        for request_id, job in self.jobs.items():
            if job["status"] in ("accepted", "running") and (only is None or request_id in only):
                job["status"] = status

    def forget(self, request_id):
        del self.jobs[request_id]
