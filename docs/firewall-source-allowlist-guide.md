# SDK HTTPS source allowlist

This operator interface controls the SDK HTTPS exposure mode and optional known
source CIDRs. Run it as root on the sandbox host after the ingress assets are
staged. It manages only project-owned TCP 443 rules. TCP 80 stays closed because
certificate issuance uses DNS-01, and existing SSH policy is never changed.

## Exposure modes

The persisted modes are explicit and reversible:

```console
sudo kitdev firewall mode public
sudo kitdev firewall mode restricted
sudo kitdev firewall mode closed
```

`public` allows TCP 443 from every IPv4 and IPv6 source and emits a warning.
Use it only as a temporary bootstrap mode. `restricted` activates the saved
source CIDRs. `closed` rejects all external HTTPS while retaining the sources
for later reuse. Every mode installs the Docker original-destination guard and
keeps TCP 80 closed. Switching modes is transactional and refuses foreign
80/443 rules.

## Commands

Add one stable public server address using a canonical host prefix:

```console
sudo kitdev firewall source add --cidr <public-ipv4>/32
sudo kitdev firewall source add --cidr <public-ipv6>/128
```

List desired sources only when the ownership manifest and live UFW/iptables
rules agree:

```console
sudo kitdev firewall source list
sudo kitdev firewall source list --json
```

Remove one exact source:

```console
sudo kitdev firewall source remove --cidr <public-ipv4>/32
```

Removing the last source while in `restricted` mode closes 443. It never
converts the policy to an unrestricted allow. Source changes made while in
`public` or `closed` mode are saved and take effect after switching to
`restricted`.

## Validation

CIDRs must be canonical. Enter a single IPv4 address as `/32` and a single IPv6
address as `/128`. Host bits, `/0`, duplicates, and overlapping ranges are
rejected. IPv4 ranges broader than `/24` and IPv6 ranges broader than `/64`
require a separate reviewed override:

```console
sudo kitdev firewall source add --cidr <reviewed-public-cidr> \
  --allow-broad-range
```

Private, loopback, link-local, multicast, reserved, and other non-global ranges
are rejected unless an operator deliberately records the exception:

```console
sudo kitdev firewall source add --cidr <reviewed-non-public-cidr> \
  --allow-non-public
```

Use both flags only when both conditions have been independently reviewed. The
flags are retained in the root-only ownership manifest for auditability.

## Failure behavior

Every command verifies active default-deny UFW policy, existing SSH allowance,
the control-plane bridge/veth firewall, public listeners, Docker publications,
and the exact `DOCKER-USER` guard. Foreign 80/443 rules, missing Docker guard
support for an IPv6 source, or manifest/live drift stop the operation without
adopting or deleting foreign policy.

Mutations and mode changes are serialized and transactional. If a rule change or manifest write
fails, the backend restores the previous source set. Treat a rollback failure
as an incident requiring direct inspection before retrying.

Before enabling `public`, verify the TLS certificate and ingress container are
ready. Opening 443 without a listener is harmless but does not complete SDK
access; starting a listener without a valid certificate is not supported.
