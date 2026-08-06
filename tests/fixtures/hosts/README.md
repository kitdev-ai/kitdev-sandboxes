# Redacted host fixtures

These files are normalized inputs for collector/evaluator unit tests. They are
not complete host inventories and must not be used to reproduce or administer
the source machines.

## `ubuntu-25.04-desktop-shared.yaml`

The fixture was derived from the read-only SSH observations described in
`docs/research/host-discovery.md`, the redacted Milestone 1 doctor result, and a
supervisor-supplied read of the loaded AMD KVM module's nested-guest support
parameter. Collection did not install packages, write files, load modules,
start or stop services, or reboot the host. The fixture records no SSH
destination, account, credential, key path, or command output.

The data is deliberately redacted and normalized:

- hostnames, host addresses, MAC addresses, serial numbers, machine/boot IDs,
  process IDs, container IDs, and account identifiers are omitted;
- routed networks are replaced with synthetic documentation or private CIDRs;
- the management listener is represented semantically, without its endpoint or
  nonstandard port;
- foreign resources retain semantic labels only where ownership/conflict tests
  need them;
- dynamic high ports and ephemeral listeners are omitted;
- available memory and filesystem capacity are rounded down to conservative
  buckets, while counters and timestamps are omitted; and
- arrays are sorted so serialization is stable.

Every synthetic CIDR is marked `synthetic: true`. These values preserve only
the route categories needed by overlap tests; they are non-replayable and are
not evidence about the source host's network.

`fixture_schema_version` belongs to the test fixture, not to the public doctor
JSON contract. `host_facts` follows the normalized collector vocabulary where
it is stable; the remaining sections are scenario inputs. Consumers should
translate the YAML into the current fact model rather than treating it as
doctor output.
