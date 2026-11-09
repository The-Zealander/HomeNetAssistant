"""Validate, queue, and run sequential music downloads in the server process."""

import threading
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse


MAX_QUEUE_ITEMS = 500
MAX_URL_LENGTH = 2048
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
SPOTIFY_HOSTS = ("spotify.com", "spotify.link")
TIDAL_HOSTS = ("tidal.com", "tidal.link")


class MusicQueueBusyError(RuntimeError):
    pass


class MusicQueue:
    """Sekventiel musikdownloadkø, som lever i serverprocessen."""

    def __init__(self, runner=None):
        self._runner = runner or self._download_url
        self._condition = threading.Condition()
        self._jobs = []
        self._logs = []
        self._paused = False
        self._worker_running = False
        self._maintenance = False

    @contextmanager
    def maintenance(self):
        """Exclude download workers while the library is being reorganized."""
        with self._condition:
            if self._maintenance or self._worker_running or any(
                job["status"] in {"queued", "processing"} for job in self._jobs
            ):
                raise MusicQueueBusyError(
                    "Vent, til musikdownloadkøen er færdig, før biblioteket gendannes."
                )
            self._maintenance = True
        try:
            yield
        finally:
            with self._condition:
                self._maintenance = False
                self._start_worker_locked()

    @staticmethod
    def classify_url(url):
        if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LENGTH:
            return "invalid"
        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")

        def matches_domain(domains):
            return any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in domains
            )

        if parsed.scheme != "https" or not hostname:
            return "invalid"
        if hostname == "music.youtube.com":
            return "youtube_music"
        if matches_domain(YOUTUBE_HOSTS):
            return "youtube"
        if matches_domain(SPOTIFY_HOSTS):
            return "spotify"
        if matches_domain(TIDAL_HOSTS):
            return "tidal"
        return "unsupported"

    @staticmethod
    def validate_url(url):
        if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LENGTH:
            raise ValueError("Hvert link skal være en URL på højst 2048 tegn.")

        parsed = urlparse(url.strip())
        if parsed.scheme != "https":
            raise ValueError("Brug et HTTPS-link fra YouTube eller YouTube Music.")
        provider = MusicQueue.classify_url(url)
        if provider == "spotify":
            raise ValueError(
                "Spotify-link genkendt. Denne kø henter ikke lyd fra Spotify; "
                "brug en lovlig lydfil eller en YouTube-kilde, du har ret til at hente."
            )
        if provider == "tidal":
            raise ValueError(
                "Tidal-link genkendt. Denne kø henter ikke lyd fra Tidal; "
                "brug en lovlig lydfil eller en YouTube-kilde, du har ret til at hente."
            )
        if provider not in {"youtube", "youtube_music"}:
            raise ValueError(
                "Ukendt linktype. Køen accepterer HTTPS-links fra YouTube og "
                "YouTube Music; Spotify- og Tidal-links genkendes, men kan ikke "
                "bruges som lydkilder."
            )
        return url.strip()

    def add(self, urls, download_video=False):
        if not urls:
            raise ValueError("Tilføj mindst ét link.")
        if len(urls) > 100:
            raise ValueError("Du kan højst føje 100 links til ad gangen.")
        if not isinstance(download_video, bool):
            raise ValueError("Videoindstillingen skal være til eller fra.")

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
                    "provider": self.classify_url(url),
                    "download_video": download_video,
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
        if self._maintenance or self._paused or self._worker_running:
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
                download_video = job["download_video"]

            try:
                failure_messages = []

                def report_progress(message):
                    self._log(job_id, message)
                    if str(message).startswith("Fejl: "):
                        failure_messages.append(str(message)[6:])

                if download_video:
                    succeeded = self._runner(
                        url,
                        report_progress,
                        download_video=True,
                    )
                else:
                    succeeded = self._runner(url, report_progress)
                if not succeeded:
                    if failure_messages:
                        details = "; ".join(failure_messages[:3])
                        remaining = len(failure_messages) - 3
                        if remaining > 0:
                            details += f"; og {remaining} flere fejl (se aktivitet)"
                        raise RuntimeError(details)
                    raise RuntimeError("Downloadet fejlede uden yderligere oplysninger.")
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
    def _download_url(url, progress_callback, download_video=False):
        from apps.music_archiver.archiver import process_url

        return process_url(url, progress_callback, download_video=download_video)
