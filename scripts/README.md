# Maintenance scripts

Small, independently testable scripts may support artifact builds, migration,
backup, and diagnostics in later milestones. Installation orchestration does not
belong in one large shell script. Every shell entrypoint uses strict mode,
bounded inputs, explicit paths, and ShellCheck.
