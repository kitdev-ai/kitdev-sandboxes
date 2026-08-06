# Changelog

All notable changes will be recorded here. The project follows Keep a
Changelog structure and will adopt semantic versioning once the first public
release boundary is defined.

## Unreleased

### Added

- Milestone 1 dependency-free typed CLI and configuration foundation, exposed
  only through the repository-local `./kitdev` launcher in this slice.
- Strictly read-only `kitdev doctor` with human and versioned JSON reports,
  explicit Ubuntu 25.04/26.04 lifecycle gates, deterministic exit categories,
  bounded host evidence, and credential redaction.
- Checked-in schemas for successful doctor reports and CLI error envelopes.
- Focused unit coverage for configuration safety, lifecycle behavior,
  non-mutation semantics, redaction, and stable output contracts.
- Bounded Linux fact composition for `doctor` and deterministic, timestamp-free,
  deliberately blocking `install --dry-run` planning with a JSON Schema.
- Milestone 0 repository scaffold, configuration contract, architecture, and
  decision records.
