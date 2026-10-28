"""Every browser-workflow action as one IOperation (agent_comms' Command
shape), so each is its own ADW (`adws/adw_browser_<name>.py`), the menu and
the agents run the very same objects, and a composed workflow is an
OperationSequence of these. Built in `catalog.py`; nothing else names them."""
