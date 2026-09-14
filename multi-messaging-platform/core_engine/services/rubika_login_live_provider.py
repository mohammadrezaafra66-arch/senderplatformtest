"""Live Rubika OTP provider adapter (L3).

Used only when RUBIKA_CANONICAL_SESSION_V1=true.
L3 does not enable that flag in production and does not request live OTP in tests.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from core_engine.services.rubika_login_state_machine import (
    OTP_INVALID,
    OTP_REQUEST_FAILED,
    ProviderOtpRequestResult,
    ProviderOtpSubmitResult,
)

logger = logging.getLogger("core_engine.services.rubika_login_live_provider")


class LiveRubikaLoginProvider:
    """Wraps rubpy send_code / sign_in. Secrets returned in secret_blob only (no OTP)."""

    async def request_otp(self, *, phone_e164: str, account_id: int) -> ProviderOtpRequestResult:
        from rubpy import Client
        from rubpy.crypto import Crypto as RubikaCrypto
        from rubpy.sessions import StringSession

        client = Client(name=StringSession(), display_welcome=False)
        await client.connect()
        try:
            result = await client.send_code(
                phone_number=phone_e164, pass_key=None, send_type="SMS"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=live_otp_request_failed account_id=%s err=%s", account_id, type(exc).__name__)
            return ProviderOtpRequestResult(ok=False, code=OTP_REQUEST_FAILED)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        status = str(getattr(result, "status", "") or "")
        if status == "SendPassKey":
            return ProviderOtpRequestResult(
                ok=False,
                code=OTP_REQUEST_FAILED,
                message="pass_key stage not yet wired in L3 live adapter",
            )
        phone_code_hash = str(getattr(result, "phone_code_hash", "") or "")
        if not phone_code_hash:
            return ProviderOtpRequestResult(ok=False, code=OTP_REQUEST_FAILED)
        public_key, private_key = RubikaCrypto.create_keys()
        provider_challenge_id = secrets.token_urlsafe(16)
        return ProviderOtpRequestResult(
            ok=True,
            code="OTP_SENT",
            provider_challenge_id=provider_challenge_id,
            secret_blob={
                "phone_code_hash": phone_code_hash,
                "public_key": public_key,
                "private_key": private_key,
            },
        )

    async def submit_otp(
        self,
        *,
        phone_e164: str,
        provider_challenge_id: str | None,
        secret_blob: dict[str, Any],
        code: str,
    ) -> ProviderOtpSubmitResult:
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15
        from rubpy import Client
        from rubpy.crypto import Crypto as RubikaCrypto
        from rubpy.sessions import StringSession

        phone_code_hash = str(secret_blob.get("phone_code_hash") or "")
        public_key = str(secret_blob.get("public_key") or "")
        private_key = str(secret_blob.get("private_key") or "")
        if not phone_code_hash or not private_key:
            return ProviderOtpSubmitResult(ok=False, code=OTP_INVALID)

        client = Client(name=StringSession(), private_key=private_key, display_welcome=False)
        await client.connect()
        try:
            result = await client.sign_in(
                phone_code=code.strip(),
                phone_number=phone_e164,
                phone_code_hash=phone_code_hash,
                public_key=public_key,
            )
            status = str(getattr(result, "status", "") or "")
            if status != "OK":
                return ProviderOtpSubmitResult(ok=False, code=OTP_INVALID)
            client.auth = RubikaCrypto.decrypt_RSA_OAEP(private_key, result.auth)
            client.key = RubikaCrypto.passphrase(client.auth)
            client.decode_auth = RubikaCrypto.decode_auth(client.auth)
            client.import_key = pkcs1_15.new(RSA.import_key(private_key.encode()))
            client.guid = str(result.user.user_guid)
            registered_phone = str(getattr(result.user, "phone", "") or phone_e164)
            await client.register_device(device_model="Afrakala-Sender-L3")
            return ProviderOtpSubmitResult(
                ok=True,
                code="OK",
                phone_number=registered_phone,
                auth=str(client.auth),
                guid=str(client.guid),
                user_agent=str(client.user_agent),
                private_key=private_key,
            )
        except Exception:  # noqa: BLE001
            return ProviderOtpSubmitResult(ok=False, code=OTP_INVALID)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
