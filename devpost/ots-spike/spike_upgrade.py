"""Spike: upgrade a pending proof through the opentimestamps library (no ots CLI).

For every pending attestation, asks THAT calendar (the URI stored in the proof,
which is a backend server, not the pool URL the stamp was sent to) for the
finished path to a Bitcoin block. Rewrites the proof only if something was
added, so a run with nothing new writes nothing and leaves no .bak behind.

Usage: python spike_upgrade.py <proof.ots>
"""
import sys

from opentimestamps.calendar import CommitmentNotFoundError, RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

path = sys.argv[1]
with open(path, "rb") as f:
    proof = DetachedTimestampFile.deserialize(StreamDeserializationContext(f))

changed = False
for msg, attestation in list(proof.timestamp.all_attestations()):
    if not isinstance(attestation, PendingAttestation):
        continue
    try:
        upgraded = RemoteCalendar(attestation.uri).get_timestamp(msg, timeout=15)
    except CommitmentNotFoundError as e:
        print(f"PENDING   {attestation.uri}: {e}")
        continue
    except Exception as e:  # unreachable or broken: say so, change nothing
        print(f"FAILED    {attestation.uri}: {type(e).__name__}: {e}")
        continue
    # Graft the calendar's answer onto the matching node of our tree.
    def graft(ts):
        global changed
        if ts.msg == msg:
            ts.merge(upgraded)
            changed = True
            return True
        return any(graft(child) for child in ts.ops.values())
    graft(proof.timestamp)
    print(f"UPGRADED  {attestation.uri}")

blocks = sorted({a.height for _, a in proof.timestamp.all_attestations() if isinstance(a, BitcoinBlockHeaderAttestation)})
print("bitcoin blocks named:", blocks or "none yet")
if changed:
    with open(path, "wb") as f:
        proof.serialize(StreamSerializationContext(f))
    print("proof rewritten")
else:
    print("nothing new, proof not rewritten")
