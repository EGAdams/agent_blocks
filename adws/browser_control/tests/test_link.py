"""WslInteropLink: finds a working interop socket, and explains an unreachable port. No Windows program runs."""
from browser_control.link import WslInteropLink


def test_a_working_default_needs_no_change():
    env = {}
    assert WslInteropLink(lambda e: True, lambda: ["/run/WSL/9_interop"], env).ensure() == (True, "") and "WSL_INTEROP" not in env


def test_a_dead_default_falls_back_to_the_first_socket_that_works():
    env, tried = {}, []
    def probe(e): tried.append(e.get("WSL_INTEROP")); return e.get("WSL_INTEROP") == "/run/WSL/3_interop"
    link = WslInteropLink(probe, lambda: ["/run/WSL/2_interop", "/run/WSL/3_interop", "/run/WSL/4_interop"], env)
    assert link.ensure() == (True, "") and env["WSL_INTEROP"] == "/run/WSL/3_interop" and tried == [None, "/run/WSL/2_interop", "/run/WSL/3_interop"]
    link.ensure(); assert len(tried) == 3                      # checked once


def test_no_working_socket_is_reported_with_the_cause():
    ok, why = WslInteropLink(lambda e: False, lambda: ["/run/WSL/2_interop"], {}).ensure()
    assert not ok and "110" in why and "not launched" in why


def test_nat_mode_is_named_and_mirrored_is_not_blamed():
    nat = WslInteropLink(lambda e: True, lambda: [], {}, run=lambda a: "nat").explain_unreachable(9222)
    assert "nat" in nat and "mirrored" in nat
    assert "mirrored" not in WslInteropLink(lambda e: True, lambda: [], {}, run=lambda a: "mirrored").explain_unreachable(9222)
