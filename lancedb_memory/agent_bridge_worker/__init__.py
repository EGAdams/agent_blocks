"""agent_bridge_worker -- the on-machine half of the ChatGPT agent bridge.

Polls the PHP gateway (`agent_bridge/gateway`) over outbound HTTPS, claims a
task with a lease, checks it against its own read-only policy, and hands it
to an on-machine agent through `agent_comms`' post office. No inbound port.
Driven by `adws/adw_bridge_worker.py`; see `agent_bridge/docs/ARCHITECTURE.md`.
"""
