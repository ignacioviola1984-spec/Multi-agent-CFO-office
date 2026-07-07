# Secret rotation - webhook HMAC with zero missed events

A webhook HMAC secret is shared between the sender (the wallet / payment provider)
and this receiver ([`sources/events/`](../sources/events/README.md)). If you rotate
it naively - swap the secret on both sides at the same instant - any event signed
with the old secret but received after you rotated the receiver is rejected, and
any event signed with the new secret but received before the sender switched is
also rejected. In a webhook world (retries, in-flight deliveries, clock skew)
that guarantees **missed events**.

The fix is a **dual-secret acceptance window**: for the duration of the rotation
the receiver accepts a signature valid under **either** the current secret **or**
the incoming (`_NEXT`) secret. As long as the window covers the sender's cutover
plus the provider's maximum retry horizon, **no event is missed**.

## The two secrets

For a source `WALLET` the receiver reads, through the `SecretsProvider`:

| Env name | Meaning |
|---|---|
| `WEBHOOK_HMAC_WALLET` | the **current** (primary) secret |
| `WEBHOOK_HMAC_WALLET_NEXT` | the **next** secret, present **only during a rotation window** |

`sources/events/receiver.py` verifies an incoming signature against **every**
secret configured for the source and accepts if **any** matches
(`verify_signature`). When `_NEXT` is absent, that is exactly single-secret
verification.

## Rotation procedure (zero missed events)

1. **Generate** the new secret `S_new` (e.g. `python -c "import secrets;
   print('whsec_'+secrets.token_hex(32))"`).
2. **Open the window on the receiver.** Set `WEBHOOK_HMAC_WALLET_NEXT = S_new`
   (leave `WEBHOOK_HMAC_WALLET = S_old`). Deploy. The receiver now accepts both.
   No sender change yet, so nothing breaks.
3. **Cut over the sender.** In the provider's dashboard/API, switch the signing
   secret to `S_new`. Deliveries now arrive signed with `S_new` and are accepted
   by the `_NEXT` secret; any still-in-flight or retried `S_old` deliveries are
   still accepted by the current secret. **This is the overlap that prevents a
   miss.**
4. **Wait out the retry horizon.** Keep the window open at least as long as the
   provider's maximum retry/backoff window (commonly up to ~24h) so every
   `S_old`-signed retry has drained.
5. **Promote and close the window.** Set `WEBHOOK_HMAC_WALLET = S_new` and
   **remove** `WEBHOOK_HMAC_WALLET_NEXT`. Deploy. Back to single-secret
   verification, now on the new secret.

At no point is there an instant where a validly-signed event would be rejected.

## Notes

- The window is **fail-safe on rejection, not on acceptance**: an event that
  matches *neither* secret is still rejected and audited (`webhook.rejected`),
  exactly as normal. The window only *adds* an accepted key, it never disables
  verification.
- Every rotation step is an ops action on secrets storage
  (`.env` locally, or the cloud secret manager via `VaultProvider`), never a code
  change.
- Rotation events themselves belong in the audit trail as an operational note,
  but the secret **values** never do - `governance/audit.py` redacts them.
- Constant-time comparison (`hmac.compare_digest`) is used for every candidate so
  rotation does not open a timing side channel.
