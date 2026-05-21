import base64
import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def _iter_secret_bytes(secret: str):
    raw_secret_bytes = secret.encode("utf-8")
    yield raw_secret_bytes

    normalized_secret = secret[6:] if secret.startswith("whsec_") else secret
    normalized_secret_bytes = normalized_secret.encode("utf-8")
    if normalized_secret_bytes != raw_secret_bytes:
        yield normalized_secret_bytes

    padded_secret = normalized_secret + "=" * (-len(normalized_secret) % 4)

    for candidate in (padded_secret, padded_secret.replace("-", "+").replace("_", "/")):
        try:
            decoded_secret = base64.b64decode(candidate, validate=True)
        except Exception:
            continue

        if decoded_secret and decoded_secret not in {
            raw_secret_bytes,
            normalized_secret_bytes,
        }:
            yield decoded_secret


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify webhook signature using HMAC SHA256.

    Args:
        payload: Raw request body bytes
        signature: Signature from webhook header (e.g., X-Inflow-Signature)
        secret: Shared secret for verification

    Returns:
        True if signature is valid, False otherwise
    """
    if not secret:
        logger.warning("No webhook secret configured, rejecting request")
        return False  # Reject if no secret configured

    if not signature:
        logger.warning("No signature provided in webhook request")
        return False

    try:
        # Common signature formats:
        # - "sha256=hexdigest"
        # - "sha256 hexdigest"
        # - Just the hexdigest
        # - Base64-encoded HMAC (x-inflow-hmac-sha256)
        normalized = signature.strip()
        if normalized.lower().startswith("sha256="):
            normalized = normalized.split("=", 1)[1].strip()
        elif normalized.lower().startswith("sha256 "):
            normalized = normalized.split(" ", 1)[1].strip()

        def matches_signature(secret_bytes: bytes) -> bool:
            logger.debug("Verifying signature with secret length %s", len(secret_bytes))
            digest = hmac.new(secret_bytes, payload, hashlib.sha256).digest()
            computed_hex = digest.hex()
            computed_b64 = base64.b64encode(digest).decode("ascii")
            computed_b64_urlsafe = base64.urlsafe_b64encode(digest).decode("ascii")
            computed_b64_urlsafe_unpadded = computed_b64_urlsafe.rstrip("=")

            if (
                hmac.compare_digest(normalized, computed_hex)
                or hmac.compare_digest(normalized, computed_b64)
                or hmac.compare_digest(normalized, computed_b64_urlsafe)
                or hmac.compare_digest(normalized, computed_b64_urlsafe_unpadded)
            ):
                return True

            # Try base64 decoding for signatures without padding or with mixed casing.
            padded = normalized + "=" * (-len(normalized) % 4)
            try:
                decoded = base64.b64decode(padded, validate=False)
                return hmac.compare_digest(decoded, digest)
            except Exception:
                return False

        for secret_bytes in _iter_secret_bytes(secret):
            if matches_signature(secret_bytes):
                return True

        logger.warning("Webhook signature verification failed")
        return False
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}", exc_info=True)
        return False
