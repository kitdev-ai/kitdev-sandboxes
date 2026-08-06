# systemd ownership

Milestone 2 will add units for the API, client proxy, worker, and maintenance
tasks. Unit names use the `kitdev-` prefix and are installed from verified
release files.

Each unit must declare a dedicated identity, dependencies, restart/timeout
behavior, writable paths, UMask, resource accounting, and tested hardening.
Worker permissions are derived from observed pinned-upstream behavior; they are
not copied blindly into less-privileged API or proxy units.
