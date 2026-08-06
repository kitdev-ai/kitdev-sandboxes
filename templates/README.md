# Guest templates

Each template directory will contain versioned build inputs, an immutable input
manifest, provenance/checksum records, and acceptance tests. Build outputs and
caches live under `/var/lib/kitdev-sandboxes`, never in this checkout.

Promotion is separate from build: a template becomes selectable only after its
tests pass. Running sandboxes continue to reference immutable prior artifacts.
