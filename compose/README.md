# Compose ownership

`control-plane/compose.yaml` is the first pinned single-host control-plane
definition derived from the validated disposable-host run. It contains the
private state services plus the temporarily containerized API, client proxy,
and one-shot migrators required by the pinned E2B revision.

Registry images use exact manifest digests. Source-built services require
locally generated `sha256:` image IDs and never pull by tag. The named
`kitdev-core` network is created and verified by the host replay before Compose
uses it as an external network. Published ports are restricted to the exact
reviewed loopback set; Redis, Loki, and internal gRPC remain unpublished.

API and client-proxy containerization is an interim containment choice because
the pinned programs use wildcard listeners. Production promotion still needs
configurable bind addresses or an equally reviewed namespace boundary. The
privileged orchestrator is not containerized.
