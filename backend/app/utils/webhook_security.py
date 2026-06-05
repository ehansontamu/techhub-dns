import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SVIX_TIMESTAMP_TOLERANCE_SECONDS = 300


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


def _verify_svix_signature(
    payload: bytes,
    signature: str,
    secret: str,
    svix_id: str | None,
    svix_timestamp: str | None,
) -> bool:
    if not svix_id or not svix_timestamp:
        return False

    try:
        timestamp = int(str(svix_timestamp).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid Svix timestamp on webhook request")
        return False

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - timestamp) > _SVIX_TIMESTAMP_TOLERANCE_SECONDS:
        logger.warning("Svix webhook timestamp outside allowed tolerance")
        return False

    signed_content = b".".join(
        [
            str(svix_id).encode("utf-8"),
            str(svix_timestamp).encode("utf-8"),
            payload,
        ]
    )

    signature_candidates: list[str] = []
    for part in str(signature).strip().split():
        candidate = part.strip()
        if not candidate:
            continue
        if candidate.startswith("v1,"):
            signature_candidates.append(candidate.split(",", 1)[1].strip())
        elif "," not in candidate:
            signature_candidates.append(candidate)

    if not signature_candidates:
        return False

    for secret_bytes in _iter_secret_bytes(secret):
        digest = hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
        for candidate in signature_candidates:
            if hmac.compare_digest(candidate, expected):
                return True

    return False


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
    *,
    svix_id: str | None = None,
    svix_timestamp: str | None = None,
) -> bool:
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
        if _verify_svix_signature(
            payload,
            signature,
            secret,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
        ):
            return True

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
