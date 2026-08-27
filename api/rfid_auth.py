"""
Per-tag NTAG215 password derivation.

WHY THIS EXISTS
    The gate trusts a tag because of its UID, and a UID is not a secret. It
    is broadcast in the clear to any reader that asks, and a "magic" NTAG215
    with a rewritable UID block costs about as much as a jeepney fare.
    Copying a genuine UID onto a blank card takes a minute, and the clone is
    indistinguishable from the original as far as /api/scan/ is concerned.

    NTAG424 DNA solves this properly with SUN/SDM: every tap emits a fresh
    MAC the server can verify, so a replayed UID is useless. We are on plain
    NTAG215, which has exactly one authentication primitive — PWD_AUTH
    (command 1Bh): a 4-byte password with a 2-byte PACK response, gated by
    the AUTH0 byte in the tag's configuration pages. A clone made from a
    UID alone does not carry the password, so a reader that performs
    PWD_AUTH can tell a genuine tag from a copy.

WHY PER-TAG AND NOT ONE SHARED PASSWORD
    One password across the whole fleet means one recovery — a lost tag, a
    dumped reader, one person who reads a config page before it is locked —
    forfeits every tag at once, and re-keying means physically recalling all
    of them. Deriving each password from the tag's own UID under a master
    key that never leaves the server means a recovered password says nothing
    about any other tag. Knowing every UID in the system (they are not
    secret, and are printed in the gate logs) still yields nothing without
    the master key.

THE DERIVATION — exact, because it must be reproducible by anything that
later needs to authenticate a tag:

    normalised = uid.strip().upper()
    digest     = HMAC-SHA256(key = RFID_MASTER_KEY.encode('utf-8'),
                             msg = normalised.encode('utf-8'))
    PWD  = digest[0:4]      # first 4 bytes  -> NTAG215 page 133 (0x85)
    PACK = digest[4:6]      # next  2 bytes  -> NTAG215 page 134 (0x86)

    The digest is truncated, never folded or XORed: bytes 0-3 and 4-5 are
    taken as they fall, and the remaining 26 bytes are discarded. Truncating
    an HMAC is sound (RFC 2104 §5) and 4 bytes is simply all the password
    NTAG215 has room for.

    The UID is upper-cased before hashing because the same physical tag must
    always derive the same password no matter which client asks. The
    registration firmware sends upper-case hex with no separators
    (uidToString() calls toUpperCase()), but a future gate reader written by
    someone else could easily send "04a2..." instead of "04A2...", and
    hashing those as different strings would produce a password that does
    not open the tag. Normalising here is cheaper than discovering that in
    the field. Note that the UID is hashed as *text*, not decoded to bytes —
    it is the same opaque string the rest of the system stores in
    StickerApplication.rfid_uid and matches on.

NOT YET IMPLEMENTED — GATE-SIDE VERIFICATION
    Writing passwords to tags does nothing to stop cloning on its own. The
    protection only becomes real when the reader at the gate performs
    PWD_AUTH and refuses a tag that fails it. That firmware does not exist
    in this project yet. When it is built it must, for each scanned UID,
    obtain the same PWD/PACK derived here (either by calling a new
    server endpoint, or by holding RFID_MASTER_KEY itself — the former is
    safer, since the latter puts the master key inside a device bolted to a
    wall), issue PWD_AUTH, and treat a wrong PACK as a denial. Until then a
    cloned UID is still accepted, exactly as it is today.
"""

import hashlib
import hmac

from django.conf import settings

# Byte offsets into the HMAC-SHA256 digest. Named rather than inlined so the
# split is stated once and any reader (or future gate firmware author) can
# see it without reverse-engineering slice literals.
PWD_OFFSET = 0
PWD_LENGTH = 4      # NTAG215 PWD is exactly 4 bytes (page 133 / 0x85)
PACK_OFFSET = PWD_OFFSET + PWD_LENGTH
PACK_LENGTH = 2     # NTAG215 PACK is exactly 2 bytes (page 134 / 0x86)


def normalise_uid(uid):
    """
    Canonical form of a UID for hashing purposes.

    Kept separate from derive_tag_credentials() so a caller comparing or
    logging UIDs can apply the identical rule instead of guessing at it.
    """
    if not isinstance(uid, str):
        raise ValueError('uid must be a string.')
    normalised = uid.strip().upper()
    if not normalised:
        raise ValueError('uid must not be empty.')
    return normalised


def derive_tag_credentials(uid):
    """
    Derive (pwd, pack) as raw bytes for one tag UID.

    Returns a 2-tuple of (4 bytes, 2 bytes). Deterministic: the same UID and
    the same RFID_MASTER_KEY always produce the same pair.

    Reads RFID_MASTER_KEY at call time rather than import time so that
    override_settings() works in tests and so a key rotation does not
    require a process restart to be picked up by this function. (Rotating
    the key in production is a different matter entirely — see below.)
    """
    normalised = normalise_uid(uid)

    master_key = getattr(settings, 'RFID_MASTER_KEY', '')
    if not master_key:
        # settings.py has no fallback for this, so an empty value here means
        # something has actively blanked it rather than simply not set it.
        # Failing loudly beats deriving every tag's password from "".
        raise ValueError(
            'RFID_MASTER_KEY is not configured. Tag passwords cannot be '
            'derived without it.'
        )

    digest = hmac.new(
        master_key.encode('utf-8'),
        normalised.encode('utf-8'),
        hashlib.sha256,
    ).digest()

    pwd = digest[PWD_OFFSET:PWD_OFFSET + PWD_LENGTH]
    pack = digest[PACK_OFFSET:PACK_OFFSET + PACK_LENGTH]
    return pwd, pack


def derive_tag_credentials_hex(uid):
    """
    Same derivation, rendered as upper-case hex for JSON transport.

    Returns {'pwd': '8 hex chars', 'pack': '4 hex chars'}. The firmware
    parses these straight into the byte arrays it writes to pages 133 and
    134, so the width is fixed and must not be trimmed of leading zeros.
    """
    pwd, pack = derive_tag_credentials(uid)
    return {
        'pwd': pwd.hex().upper(),
        'pack': pack.hex().upper(),
    }
