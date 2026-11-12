"""scala.storage -- Scala files a scan on BATI, R46 or SQMC, by scan id, as a verified copy.

    storage_targets.json  the machines, their hostnames, roots and approved folders (configuration, not code)
    targets.py            the registry, and person words -> an approved folder, or a refusal
    remote_helper.py      runs ON the destination: identity, path fences, no-overwrite write, SHA-256
    transports.py         that helper locally, or over the existing key-authenticated ssh
    service.py            ScanStorage: list/store/retrieve/verify/health/status
    records.py            audit trail (JSONL), run records, staged copies for retry
    scan_source.py        the original bytes, from the scanner API by scan id

The ADW is `adws/adw_scala_storage.py`; the Bridge agent is `agent_bridge_worker/scala_storage_agent.py`.
"""
