# Security policy

## Supported versions

No version is currently supported for production use. The project is in its
architecture milestone and does not yet provide a runnable deployment.

## Reporting a vulnerability

Do not file public issues containing exploit details, credentials, host
addresses, or logs with secrets. Until a private project security contact is
published, contact the repository owner privately through the hosting
platform. Include the affected revision, impact, reproduction steps, and any
suggested mitigation.

Please allow a reasonable embargo for investigation and coordinated release.
The project will credit reporters unless anonymity is requested.

## Security boundary

Sandbox workloads are untrusted. A sandbox escape, access to host or control
plane services, cross-sandbox access, bypass of resource limits, secret
disclosure, unauthenticated public endpoint, or destructive uninstall behavior
is considered security-sensitive.

The design threat model will live in `docs/security-model.md`; it is a required
deliverable before runtime components are considered production-ready.
