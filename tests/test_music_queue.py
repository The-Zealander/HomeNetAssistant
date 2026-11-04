import threading
import time
import unittest

from apps.music_archiver.queue import MusicQueue


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


if __name__ == "__main__":
    unittest.main()
