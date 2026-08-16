# Test Evidence

Primary file: `tests/core_engine/test_rubika_phase1_account_session.py`

| Requirement | Test |
|-------------|------|
| A required_session_type | `test_required_session_type_*`, `test_normalize_*` |
| B bot_api validation | `test_bot_api_register_valid_empty_malformed` |
| C envelope | `test_user_envelope_version_roundtrip_and_rejects` |
| D persistence | `test_bot_api_persistence_roundtrip_and_restart` |
| E restart reload | bot + user restart tests |
| F readiness | `test_readiness_*` |
| G OTP success | existing `test_rubika_user_login_flow_success` |
| H OTP failure | `test_otp_wrong_code_does_not_corrupt`, `test_otp_expired_token` |
| I duplicate verify | `test_otp_duplicate_verify_rejected` |
| J duplicate session | `test_duplicate_session_latest_row_wins` |
| K transitions | `test_account_status_transitions_pool` |
| L config isolation | mode pin fixtures + wrong-mode register reject |

Regression pins: no `os.name` monkeypatch; Redis reset fixture; explicit `RUBIKA_DELIVERY_MODE`.
