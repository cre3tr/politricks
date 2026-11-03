"""Spike: stamp one file through the opentimestamps library directly (no ots CLI).

Mirrors what otsclient's `stamp` does: SHA-256 the file, append a random nonce,
hash again, submit that digest to calendars, keep every answer, and write
<file>.ots. Prints which calendars answered and the proof's file digest.
Needs answers from at least 2 calendars, or writes nothing.

Usage: python spike_stamp.py <file>
"""
import hashlib
import os
import sys

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

CALENDARS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
]

path = sys.argv[1]
with open(path, "rb") as f:
    digest = hashlib.sha256(f.read()).digest()

detached = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
nonce_stamp = detached.timestamp.ops.add(OpAppend(os.urandom(16)))
merkle_tip = nonce_stamp.ops.add(OpSHA256())

answered = 0
for url in CALENDARS:
    try:
        reply = RemoteCalendar(url).submit(merkle_tip.msg, timeout=15)
        merkle_tip.merge(reply)
        answered += 1
        print(f"answered: {url}")
    except Exception as e:  # report every failure, never hide one
        print(f"FAILED:   {url}: {type(e).__name__}: {e}")

print(f"calendars answered: {answered} of {len(CALENDARS)}")
if answered < 2:
    print("too few answers, no proof written")
    sys.exit(1)

with open(path + ".ots", "wb") as f:
    detached.serialize(StreamSerializationContext(f))
print("file digest in proof:", detached.file_digest.hex())
print("file sha256:         ", digest.hex())
print("attestations:", [type(a).__name__ for _, a in detached.timestamp.all_attestations()])
