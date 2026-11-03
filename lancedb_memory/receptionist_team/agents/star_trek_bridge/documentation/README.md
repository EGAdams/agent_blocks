# star_trek_bridge

Toyota's read-only door to the Star Trek Bridge. Routed with `ROUTE: star_trek_bridge | <question>`,
`ROUTE: star_trek_bridge | <bridge agent>: <question>` (e.g. `hello-world: say hello`),
`ROUTE: star_trek_bridge | which agents are on the bridge`,
or `ROUTE: star_trek_bridge | status t_<id>` / `result t_<id>`.
Run by `adapters/bridge_runner.BridgeDelegationRunner` -- no model in the path.
See `../../bridge_client/README.md`.
