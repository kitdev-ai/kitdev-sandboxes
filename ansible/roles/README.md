# Planned roles

The empty directories reserve these narrow ownership boundaries:

- `preflight`: fact collection and hard requirement gates;
- `host_packages`: approved package and repository state;
- `host_kernel`: project module/sysctl/huge-page drop-ins;
- `docker`: compatibility and project integration, never Docker ownership;
- `e2b_sources`: verified immutable upstream source trees;
- `e2b_datastores`: private Compose state services;
- `e2b_api`, `e2b_proxy`, `e2b_orchestrator`: separate service identities;
- `e2b_templates`: guest artifact build/promotion;
- `networking` and `firewall`: links, namespaces, policy, and exact rollback;
- `observability`, `backup`, and `validation`: later operational concerns.
