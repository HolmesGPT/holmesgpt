"""Holmes's X25519 key pair, derived from the Robusta signing key.

The public key is published in HolmesStatus.metadata.holmes_public_key, so the
platform can store data that only this Holmes can read. A sealed value is
base64(ephemeral public key (32) | nonce (12) | AES-GCM ciphertext); the relay
seals with the same format.
"""

import base64
import logging
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from holmes.utils.env import get_env_replacement

logger = logging.getLogger(__name__)

_KEY_LEN = 32
_NONCE_LEN = 12


def get_signing_key() -> Optional[str]:
    from holmes.config import Config

    key = Config.get_robusta_global_config_value("signing_key")
    if not key:
        return None
    # Generated values set signing_key to "{{ env.SIGNING_KEY }}"; without
    # resolving it every such install would share that literal as its key.
    try:
        return get_env_replacement(key)
    except ValueError:
        logger.warning(
            "global_config.signing_key references an env var that is not set"
        )
        return None


def _private_key(signing_key: str) -> X25519PrivateKey:
    seed = HKDF(
        algorithm=SHA256(), length=_KEY_LEN, salt=b"holmes-key", info=b"x25519"
    ).derive(signing_key.encode())
    return X25519PrivateKey.from_private_bytes(seed)


def _raw(public_key: X25519PublicKey) -> bytes:
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def get_public_key() -> Optional[str]:
    signing_key = get_signing_key()
    if not signing_key:
        return None
    return base64.b64encode(_raw(_private_key(signing_key).public_key())).decode()


def open_sealed(sealed: str) -> bytes:
    signing_key = get_signing_key()
    if not signing_key:
        raise ValueError("cannot decrypt: no signing key is configured")
    private_key = _private_key(signing_key)
    data = base64.b64decode(sealed)
    ephemeral = data[:_KEY_LEN]
    nonce = data[_KEY_LEN : _KEY_LEN + _NONCE_LEN]
    shared = private_key.exchange(X25519PublicKey.from_public_bytes(ephemeral))
    key = HKDF(
        algorithm=SHA256(),
        length=_KEY_LEN,
        salt=None,
        info=b"holmes-sealed" + ephemeral + _raw(private_key.public_key()),
    ).derive(shared)
    return AESGCM(key).decrypt(nonce, data[_KEY_LEN + _NONCE_LEN :], None)
