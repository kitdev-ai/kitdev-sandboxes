# Role ownership

Implemented in the host-prerequisite slice:

- `preflight`: immutable platform, capability, APT trust and collision gates;
- `host_packages`: approved packages plus immutable pre-change state capture;
- `host_identity`: three fixed non-login identities; only the worker joins KVM;
- `host_kernel`: project module, NBD and sysctl drop-ins plus live convergence;
- `host_manifest`: deterministic final versions and managed-file digests;
- `host_remove`: authenticated guarded restoration from the pre-change state.

Reserved for later milestones:

- `docker`: compatibility and project integration, never Docker ownership;
- `e2b_sources`: verified immutable upstream source trees;
- `e2b_datastores`: private Compose state services;
- `e2b_api`, `e2b_proxy`, `e2b_orchestrator`: separate service identities;
- `e2b_templates`: guest artifact build/promotion;
- `networking` and `firewall`: links, namespaces, policy, and exact rollback;
- `observability`, `backup`, and `validation`: later operational concerns.
