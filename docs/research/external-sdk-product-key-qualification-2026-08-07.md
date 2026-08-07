# External SDK product key qualification

Date: 2026-08-07

Status: the dedicated project credential is active and loopback-authenticated.
Public HTTPS and external SDK use remain blocked on ingress qualification.

## Scope

This gate issued a dedicated credential for software using the official E2B
TypeScript SDK from another server. It used the exact `kitdev api-key` CLI from
pushed commit `daa71ce` on the Ubuntu 26.04 OVH development lab. No raw key,
admin token, team UUID, host address, or private endpoint was printed or
recorded.

The exact eligible team selector was:

```text
kitdev-browser-heavy-team
```

Read-only discovery found three eligible teams and exactly one match for that
slug. Before mutation, the loopback API health check passed and no Firecracker
process was active.

## Durable credential record

The operator-owned source files are:

```text
/etc/kitdev-sandboxes/secrets/external-sdk-product.key
/etc/kitdev-sandboxes/secrets/external-sdk-product.key.metadata.json
```

Both are regular `root:root` files, mode `0600`, link count one, beneath a
`root:root` mode-`0700` directory. The raw file is 45 bytes, matching the
expected key plus newline. Its nonsecret key ID is:

```text
d63b17ec-07cb-4577-b33d-e576b01be5e9
```

The raw key remains only in the protected source file. The metadata contains
the key ID, mask, ownership binding, and recovery state, not the raw value.

## Results

| Predicate | Result |
| --- | --- |
| exact team slug resolution | pass; one match |
| initial create | `created` |
| key and metadata invariants | pass; regular, `root:root`, `0600`, link count one |
| project authentication | `authenticated` via bounded sandbox list |
| identical create rerun | `existing`; same key ID |
| remote list | exact key ID present once |
| list disclosure | structured mask only; no raw key field/value |
| metadata state | `active` |
| metadata raw-key regex scan | zero matches |
| recent journal raw-key regex scan | zero matches |

The exact staging archive was root-owned and mode `0700`. It was used only to
execute committed bytes and was removed after the gate. The persistent key and
metadata were deliberately retained for secure product installation,
idempotent verification, rotation, and exact revocation.

## Qualification boundary

This proves host-local issuance and authentication against the loopback API.
It does not prove public TLS, API reachability from the product server,
wildcard sandbox traffic, a stable template alias, or the external SDK matrix.
At the time of this gate, public TCP 80 and 443 were still unreachable.

The operator has selected unrestricted public TCP 443 as a temporary
development posture once valid TLS ingress is ready. That mode is not live at
this checkpoint and is not the recommended steady state. It increases the API
and wildcard traffic exposure to all Internet sources even though project
authentication remains required. Apply it only together with valid TLS,
closed TCP 80, internal-port isolation, rate limits, and monitoring; replace it
with the source-restricted policy after the external client address is stable.

## Rotation and revocation boundary

Do not revoke this credential before the product server has installed it and
the external authentication smoke test passes. For rotation, create a new
path/name, securely install and prove the replacement, update the product
service, and then revoke this exact key ID with duplicate confirmation and the
metadata-bound `--delete-key-file` flow. Revocation invalidates upstream
authentication immediately; source-file deletion happens only after the
durable revoked metadata transition.

