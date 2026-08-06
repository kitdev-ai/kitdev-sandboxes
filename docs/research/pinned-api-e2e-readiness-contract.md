# Pinned API E2E readiness contract

Date: 2026-08-06

Scope: independent review of the reusable API-to-Firecracker E2E verifier at
E2B infrastructure commit
`882a3b4786755db9e94be3297de6827f9100ce5e`. No credential or live runtime
identifier is recorded here.

## Exact API shapes

The pinned OpenAPI schema and handler source establish these response shapes:

- `GET /nodes` returns a top-level array of Node objects. The node identifier
  field is `id` and the ready status is the literal `ready`.
- Node identifiers are configured operator strings, not UUIDs. The repository
  replay configuration currently pins `NODE_ID=kitdev-e2b-01`; the verifier
  should derive the expected value from the authenticated installed
  `orchestrator.env.expected` file and compare it exactly.
- `GET /nodes/{nodeID}` returns one NodeDetail object. OpenAPI requires
  `cachedBuilds` as an array of build-ID strings, but the current disposable
  lab returned JSON `null` before sandbox creation. Cache residency is
  therefore not a valid pre-create readiness gate for this pinned live stack.
- `GET /sandboxes` returns a top-level array of ListedSandbox objects. Its
  identifier field is `sandboxID`.
- `POST /sandboxes` returns a top-level Sandbox object. The pinned API creates
  the ID as the literal prefix `i` followed by `uniuri.UUIDLen` (20) lowercase
  alphanumeric characters, so the exact verifier pattern is
  `^i[a-z0-9]{20}$`.

## Seed visibility predicate

`GET /templates/{templateID}` cannot be queried with the known build UUID: its
handler looks up an `envs.id`, while the copied build UUID is an
`env_builds.id`. Use API-key-authenticated `GET /templates`, whose exact
response is a top-level array of Template objects.

For build `2d9a8389-f5f5-4449-b0eb-e1d364ee98ae`, readiness requires exactly
one array item with all of these values:

| Field | Required value |
|---|---|
| `buildID` | `2d9a8389-f5f5-4449-b0eb-e1d364ee98ae` |
| `buildStatus` | `ready` |
| `envdVersion` | `0.6.13` |
| `cpuCount` | `2` |
| `memoryMB` | `1024` |
| `diskSizeMB` | `3722` |
| `public` | `false` |
| `templateID` | lowercase alphanumeric, 16 to 32 characters |

The returned `templateID` may then be used with `GET /templates/{templateID}`.
That endpoint returns one TemplateWithBuilds object; its `builds` array should
contain exactly one matching build item with the same build UUID, `ready`
status, resource values, and envd version.

A credential-safe live query on the disposable host confirmed that
`GET /templates` returned a top-level array with exactly one target item, the
document exposed the OpenAPI field names above, and every predicate in the
table passed. The query printed only types, field names, counts, and predicate
results; it did not print the team ID, template ID, node ID, API key, or admin
token.

The pre-create gate is thus: exact orchestrator unit active, health HTTP 200,
exactly one expected node reporting `ready`, and the exact API template/build
predicate above. It must not depend on `cachedBuilds` being populated before
the first create.

## Primary sources

- [Pinned OpenAPI schema](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/spec/openapi.yml)
- [Pinned sandbox create handler](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/handlers/sandbox_create.go)
- [Pinned template get handler](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/api/internal/handlers/template_get.go)
- [Pinned ID implementation](https://github.com/e2b-dev/infra/blob/882a3b4786755db9e94be3297de6827f9100ce5e/packages/shared/pkg/id/id.go)
