"""Exercise download queue validation, worker state, and library maintenance."""

import threading
import time
import unittest

from apps.music_archiver.queue import MusicQueue, MusicQueueBusyError


class MusicQueueTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_urls_are_restricted_to_https_youtube_domains(self):
        for url in (
            "http://youtube.com/watch?v=video",
            "https://youtube.com.attacker.test/watch?v=video",
            "https://example.com/watch?v=video",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MusicQueue.validate_url(url)

        self.assertEqual(
            MusicQueue.validate_url("https://music.youtube.com/playlist?list=album"),
            "https://music.youtube.com/playlist?list=album",
        )

    def test_links_are_classified_by_exact_provider_domain(self):
        examples = {
            "https://music.youtube.com/watch?v=video": "youtube_music",
            "https://www.youtube.com/watch?v=video": "youtube",
            "https://youtu.be/video": "youtube",
            "https://open.spotify.com/track/track-id": "spotify",
            "https://spotify.link/shared": "spotify",
            "https://listen.tidal.com/album/album-id": "tidal",
            "https://tidal.link/shared": "tidal",
            "https://youtube.com.attacker.test/watch?v=video": "unsupported",
            "http://youtube.com/watch?v=video": "invalid",
        }
        for url, expected_provider in examples.items():
            with self.subTest(url=url):
                self.assertEqual(MusicQueue.classify_url(url), expected_provider)

    def test_spotify_and_tidal_links_are_identified_but_not_queued_as_audio(self):
        examples = {
            "https://open.spotify.com/track/track-id": "Spotify-link genkendt",
            "https://listen.tidal.com/album/album-id": "Tidal-link genkendt",
        }
        for url, message in examples.items():
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, message):
                MusicQueue.validate_url(url)

    def test_queue_processes_one_link_at_a_time_in_order(self):
        started = []
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()

        def runner(url, log):
            started.append(url)
            if len(started) == 1:
                first_started.set()
                release_first.wait(timeout=3)
            else:
                second_started.set()
            return True

        queue = MusicQueue(runner=runner)
        queue.add([
            "https://youtube.com/watch?v=one",
            "https://youtu.be/two",
        ])
        self.assertTrue(first_started.wait(timeout=3))
        self.assertEqual(started, ["https://youtube.com/watch?v=one"])

        release_first.set()
        self.assertTrue(second_started.wait(timeout=3))
        self.assertTrue(self.wait_for(
            lambda: all(job["status"] == "completed" for job in queue.snapshot()["jobs"])
        ))
        self.assertEqual(started, ["https://youtube.com/watch?v=one", "https://youtu.be/two"])

    def test_video_option_is_kept_on_job_and_passed_to_runner(self):
        requests = []

        def runner(url, report_progress, *, download_video=False):
            requests.append((url, download_video))
            return True

        queue = MusicQueue(runner=runner)
        added = queue.add(
            ["https://youtube.com/watch?v=video"],
            download_video=True,
        )

        self.assertTrue(self.wait_for(
            lambda: queue.snapshot()["jobs"][0]["status"] == "completed"
        ))
        self.assertTrue(added[0]["download_video"])
        self.assertEqual(
            requests,
            [("https://youtube.com/watch?v=video", True)],
        )

    def test_pause_holds_pending_jobs_until_resumed(self):
        first_started = threading.Event()
        finish_first = threading.Event()
        second_started = threading.Event()
        processed = []

        def runner(url, log):
            processed.append(url)
            if len(processed) == 1:
                first_started.set()
                finish_first.wait(timeout=3)
            else:
                second_started.set()
            return True

        queue = MusicQueue(runner=runner)
        queue.add(["https://youtube.com/watch?v=one", "https://youtube.com/watch?v=two"])
        self.assertTrue(first_started.wait(timeout=3))
        queue.pause()
        finish_first.set()
        self.assertTrue(self.wait_for(lambda: queue.snapshot()["jobs"][0]["status"] == "completed"))
        self.assertFalse(second_started.wait(timeout=0.1))
        self.assertEqual(queue.snapshot()["jobs"][1]["status"], "queued")

        queue.resume()
        self.assertTrue(second_started.wait(timeout=3))
        self.assertTrue(self.wait_for(
            lambda: all(job["status"] == "completed" for job in queue.snapshot()["jobs"])
        ))

    def test_library_maintenance_defers_downloads_and_rejects_an_active_queue(self):
        started = threading.Event()
        queue = MusicQueue(runner=lambda url, log: started.set() or True)
        queue.pause()

        with queue.maintenance():
            queue.add(["https://youtu.be/restore"])
            self.assertFalse(started.is_set())
            with self.assertRaises(MusicQueueBusyError):
                with queue.maintenance():
                    pass

        self.assertFalse(started.is_set())
        queue.resume()
        self.assertTrue(started.wait(timeout=3))

    def test_queued_job_can_be_removed_and_processing_job_cannot(self):
        started = threading.Event()
        release = threading.Event()

        def runner(url, log):
            started.set()
            release.wait(timeout=3)
            return True

        queue = MusicQueue(runner=runner)
        added = queue.add(["https://youtube.com/watch?v=one", "https://youtube.com/watch?v=two"])
        self.assertTrue(started.wait(timeout=3))
        with self.assertRaises(ValueError):
            queue.remove(added[0]["id"])
        self.assertTrue(queue.remove(added[1]["id"]))
        release.set()
        self.assertTrue(self.wait_for(lambda: queue.snapshot()["jobs"][0]["status"] == "completed"))

    def test_failed_job_shows_specific_downloader_error(self):
        def runner(url, report_progress):
            report_progress("Fejl: yt-dlp fejlede: Video unavailable in this region")
            return False

        queue = MusicQueue(runner=runner)
        queue.add(["https://youtube.com/watch?v=unavailable"])

        self.assertTrue(self.wait_for(lambda: queue.snapshot()["jobs"][0]["status"] == "failed"))
        snapshot = queue.snapshot()
        self.assertEqual(
            snapshot["jobs"][0]["message"],
            "yt-dlp fejlede: Video unavailable in this region",
        )
        self.assertEqual(
            snapshot["logs"][0]["message"],
            "Fejl: yt-dlp fejlede: Video unavailable in this region",
        )


if __name__ == "__main__":
    unittest.main()
