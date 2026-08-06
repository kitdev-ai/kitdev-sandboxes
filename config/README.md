# Configuration contract

`default.yaml` is the versioned, non-secret baseline. The installed copy will
live at `/etc/kitdev-sandboxes/config.yaml` and must validate against
`schema.json` before any mutation occurs.

Precedence is expected to be defaults, installed operator configuration, then
explicit CLI flags. Unknown keys are rejected. Secrets never belong in this
schema; generated secrets will live in a root-owned `secrets.env` with mode
`0600` and will be preserved on convergent reruns.

`deployment.lifecycle_mode` defaults to `production`. Preflight permits that
mode only on Ubuntu 26.04 LTS. Ubuntu 25.04 is recognized for explicit
`development` or `migration` use because it is end-of-life; selecting
`production` on 25.04 is a blocking, non-mutating validation failure.
