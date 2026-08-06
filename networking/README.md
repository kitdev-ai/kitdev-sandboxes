# Networking ownership

This directory will contain declarative network policy and generated nftables
inputs after host and upstream discovery are accepted. The policy is described
in ADR 0003.

Generated rules own only `inet kitdev_sandboxes`; they never flush or replace
host tables. Address pools are conflict-checked against routes, VPNs, Docker,
NetworkManager, and existing bridges. Both IPv4 and IPv6 outcomes are explicit.
