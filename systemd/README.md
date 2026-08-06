# systemd ownership

`kitdev-e2b-orchestrator.service` is the project-owned persistent unit for the
pinned host orchestrator/template-manager binary. Its installer renders the
verified core-network gateway into a root-only environment file, publishes an
exact preflight, enables the unit, and can verify every installed byte without
changing state.

The orchestrator remains root because the pinned upstream runtime owns network
namespaces, veth/TAP and NBD devices, mounts, cgroups, and firewall rules. Its
preflight therefore treats exact artifacts, high-range worker identity,
hugepage capacity, network non-overlap, firewall rules, and environment schema
as start gates. This is an explicit architecture constraint, not a general
identity pattern for future API, proxy, worker, or maintenance units.
