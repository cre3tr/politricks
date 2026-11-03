"""Spike: does a proof bind to its file? Offline: no calendar, no Bitcoin node.

Compares the proof's file digest with the SHA-256 of the file and of a copy
with one byte flipped. The flipped copy must NOT match (the control).

Usage: python spike_check.py <file> <proof.ots>
"""
import hashlib
import sys

from opentimestamps.core.serialize import StreamDeserializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

path, ots_path = sys.argv[1], sys.argv[2]
with open(ots_path, "rb") as f:
    proof = DetachedTimestampFile.deserialize(StreamDeserializationContext(f))

data = bytearray(open(path, "rb").read())
real = hashlib.sha256(data).digest()
data[len(data) // 2] ^= 0x01
flipped = hashlib.sha256(data).digest()

print("proof digest == file sha256:     ", proof.file_digest == real)
print("proof digest == one-byte-flipped:", proof.file_digest == flipped)
print("pending calendars:", sorted(a.uri for _, a in proof.timestamp.all_attestations()))
sys.exit(0 if proof.file_digest == real and proof.file_digest != flipped else 1)
