"""Tiny invented carriers only; no real executable/network is opened or run."""
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import hosted
import reference_transport as transport


class TransportTests(unittest.TestCase):
    def test_exact_previously_reviewed_reader_is_vendored(self):
        self.assertEqual(hashlib.sha256((hosted.HERE / "reference_transport.py").read_bytes()).hexdigest(),
                         "beab24bbfff53e4f919451a1114e3900fd68eb99ee71bd618f09cda080a0c75d")
        source = (hosted.HERE / "hosted.py").read_text()
        self.assertNotIn("urlretrieve", source)
        self.assertIn("transport.REFERENCE.verify(size, digest)", source)
        self.assertIn("network_receipt = transport.extract_network(netdir)", source)
        self.assertIn('build / "src/nn-18cd32e11a9c.nnue"', source)

    def test_transfer_checks_both_size_and_hash_without_retry(self):
        payload = b"invented-input-only"
        output = io.BytesIO()
        size, digest = transport.transfer(io.BytesIO(payload), output, len(payload))
        transport.PinnedFile("synthetic", len(payload), hashlib.sha256(payload).hexdigest()).verify(size, digest)
        self.assertEqual(output.getvalue(), payload)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            transport.transfer(io.BytesIO(payload + b"extra"), io.BytesIO(), len(payload))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            transport.PinnedFile("synthetic", len(payload), "0" * 64).verify(size, digest)

    def test_exact_embedded_extraction_on_invented_tiny_image(self):
        header = b"NTST"
        payload = header + b"0000invented-network-payload"
        prefix_sha = hashlib.sha256(payload[:8]).hexdigest()
        image = b"invented-not-a-PE-" + payload + b"tail"
        with TemporaryDirectory(prefix="fc2-transport-mock-", dir=hosted.HERE.parents[2]) as name:
            directory = Path(name)
            (directory / "reference.exe").write_bytes(image)
            reference = transport.PinnedFile("reference.exe", len(image), hashlib.sha256(image).hexdigest())
            network = transport.PinnedFile("parent.nnue", len(payload), hashlib.sha256(payload).hexdigest())
            with patch.object(transport, "REFERENCE", reference), patch.object(transport, "NETWORK", network), \
                 patch.object(transport, "NETWORK_HEADER", header), patch.object(transport, "PREFIX_SIZE", 8), \
                 patch.object(transport, "PREFIX_SHA256", prefix_sha):
                receipt = transport.extract_network(directory)
            self.assertEqual((directory / "parent.nnue").read_bytes(), payload)
            self.assertEqual(receipt["offset_bytes"], len(b"invented-not-a-PE-"))
            self.assertEqual(receipt["sha256"], hashlib.sha256(payload).hexdigest())

    def test_duplicate_matching_prefix_is_not_guessed(self):
        payload = b"NTST0000invented-network-payload"
        with TemporaryDirectory(prefix="fc2-transport-mock-", dir=hosted.HERE.parents[2]) as name:
            directory = Path(name)
            (directory / "reference.exe").write_bytes(payload + b"between" + payload)
            network = transport.PinnedFile("parent.nnue", len(payload), hashlib.sha256(payload).hexdigest())
            with patch.object(transport, "NETWORK", network), patch.object(transport, "NETWORK_HEADER", b"NTST"), \
                 patch.object(transport, "PREFIX_SIZE", 8), patch.object(transport, "PREFIX_SHA256", hashlib.sha256(payload[:8]).hexdigest()):
                with self.assertRaisesRegex(ValueError, "Multiple"):
                    transport.extract_network(directory)
            self.assertFalse((directory / "parent.nnue").exists())


if __name__ == "__main__": unittest.main()
