# Compose ownership

Milestone 2 will add a `kitdev-sandboxes` Compose project for the state services
required by the pinned E2B revision. Definitions are intentionally absent until
upstream discovery establishes exact images, versions, ports, health checks,
and storage requirements.

Compose services will use private networks and explicit project labels. No
datastore or administration port binds publicly by default. Host-integrated API,
proxy, and worker services do not belong in Compose.
