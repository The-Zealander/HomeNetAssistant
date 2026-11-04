import threading
import uuid
from urllib.parse import urlparse


MAX_QUEUE_ITEMS = 500
MAX_URL_LENGTH = 2048
ALLOWED_HOSTS = ("youtube.com", "youtu.be")


class MusicQueue:
    """Sekventiel musikdownloadkø, som lever i serverprocessen."""

    def __init__(self, runner=None):
        self._runner = runner or self._download_url
        self._condition = threading.Condition()
        self._jobs = []
        self._logs = []
        self._paused = False
        self._worker_running = False

    @staticmethod
    def validate_url(url):
        if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LENGTH:
            raise ValueError("Hvert link skal være en URL på højst 2048 tegn.")

        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        allowed = any(hostname == host or hostname.endswith(f".{host}") for host in ALLOWED_HOSTS)
        if parsed.scheme != "https" or not allowed:
            raise ValueError("Brug et HTTPS-link fra YouTube eller YouTube Music.")
        return url.strip()

    def add(self, urls):
        if not urls:
            raise ValueError("Tilføj mindst ét link.")
        if len(urls) > 100:
            raise ValueError("Du kan højst føje 100 links til ad gangen.")

        cleaned_urls = [self.validate_url(url) for url in urls]
        with self._condition:
            pending_count = sum(job["status"] in {"queued", "processing"} for job in self._jobs)
            if pending_count + len(cleaned_urls) > MAX_QUEUE_ITEMS:
                raise ValueError(f"Køen kan højst indeholde {MAX_QUEUE_ITEMS} aktive links.")

            added = []
            for url in cleaned_urls:
                job = {
                    "id": uuid.uuid4().hex,
                    "url": url,
                    "status": "queued",
                    "message": "Venter",
                }
                self._jobs.append(job)
                added.append(dict(job))
            self._start_worker_locked()
            return added

    def snapshot(self):
        with self._condition:
            return {
                "paused": self._paused,
                "running": self._worker_running,
                "jobs": [dict(job) for job in self._jobs],
                "logs": list(self._logs),
            }

    def pause(self):
        with self._condition:
            self._paused = True
            return self.snapshot()

    def resume(self):
        with self._condition:
            self._paused = False
            self._condition.notify_all()
            self._start_worker_locked()
            return self.snapshot()

    def remove(self, job_id):
        with self._condition:
            job = next((item for item in self._jobs if item["id"] == job_id), None)
            if job is None:
                return False
            if job["status"] != "queued":
                raise ValueError("Kun links, der venter i køen, kan fjernes.")
            self._jobs.remove(job)
            return True

    def clear_finished(self):
        with self._condition:
            self._jobs = [job for job in self._jobs if job["status"] in {"queued", "processing"}]
            return self.snapshot()

    def _start_worker_locked(self):
        if self._paused or self._worker_running:
            return
        if not any(job["status"] == "queued" for job in self._jobs):
            return
        self._worker_running = True
        threading.Thread(target=self._work, name="music-download-queue", daemon=True).start()

    def _work(self):
        while True:
            with self._condition:
                while self._paused:
                    self._condition.wait()
                job = next((item for item in self._jobs if item["status"] == "queued"), None)
                if job is None:
                    self._worker_running = False
                    return
                job["status"] = "processing"
                job["message"] = "Downloader"
                job_id = job["id"]
                url = job["url"]

            try:
                succeeded = self._runner(
                    url,
                    lambda message: self._log(job_id, message),
                )
                if not succeeded:
                    raise RuntimeError("Et eller flere numre i dette link fejlede.")
            except Exception as error:
                self._finish(job_id, "failed", str(error))
            else:
                self._finish(job_id, "completed", "Færdig")

    def _finish(self, job_id, status, message):
        with self._condition:
            job = next((item for item in self._jobs if item["id"] == job_id), None)
            if job is not None:
                job["status"] = status
                job["message"] = message

    def _log(self, job_id, message):
        with self._condition:
            self._logs.append({"job_id": job_id, "message": str(message)})
            self._logs = self._logs[-300:]

    @staticmethod
    def _download_url(url, progress_callback):
        from apps.music_archiver.archiver import process_url

        return process_url(url, progress_callback)
