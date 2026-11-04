import unittest

from apps.network_scanner import scan_network


class NetworkScannerPlaceholderTests(unittest.TestCase):
    def test_scanning_fails_clearly_without_network_activity(self):
        with self.assertRaisesRegex(NotImplementedError, "ingen netværksaktivitet"):
            scan_network()


if __name__ == "__main__":
    unittest.main()
