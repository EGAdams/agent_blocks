"""The guard chain: every reason a message does not get typed, and the reason a caller is told.

    cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q chatgpt_relay/tests
"""
from __future__ import annotations

from chatgpt_relay.guards import (CredentialGuard, GuardChain, LengthGuard, LoopGuard, MAX_MESSAGE,
                                  NonEmptyGuard, SelfTalkGuard, default_guards)
from chatgpt_relay.models import RelayInstance

BATI = RelayInstance(label="bati", base_url="http://x", owner="EG")
TRUSTED = RelayInstance(label="trusty", base_url="http://y", trusted_sender=True)


def check(message, **args):
    return default_guards().check(message, args, BATI)


def test_an_empty_message_is_refused_as_empty_and_not_as_anything_else():
    verdict = check("   \n ")
    assert not verdict.allowed and verdict.guard == "non_empty"
    assert "no message" in verdict.reason


def test_ordinary_words_pass():
    assert check("What is the Kent County Register of Deeds phone number?").allowed


def test_a_password_never_reaches_the_page_and_the_reason_says_where_to_put_it_instead():
    for message in ("the password is hunter2", "my PIN is 4821", "here's the one-time code 889123",
                    "use api key sk-abc", "-----BEGIN RSA PRIVATE KEY-----"):
        verdict = check(message)
        assert not verdict.allowed and verdict.guard == "credential", message
        assert "permanent history" in verdict.reason and "variable name" in verdict.reason


def test_a_credential_is_refused_before_length_is_even_considered():
    """Order is the behaviour: a 20000-character paste that contains a password is reported as a
    credential, because that is the part that matters."""
    verdict = check("password: " + "z" * (MAX_MESSAGE + 50))
    assert verdict.guard == "credential"


def test_too_long_is_refused_with_the_limit_in_the_sentence():
    verdict = check("z" * (MAX_MESSAGE + 1))
    assert not verdict.allowed and verdict.guard == "length"
    assert str(MAX_MESSAGE) in verdict.reason


def test_a_relayed_message_stops_after_one_hop_because_nothing_is_trusted_yet():
    """ChatGPT may message another instance of itself; two instances that may do it without limit can
    do it forever. The hop count is the limit, and only a trusted origin raises it."""
    assert check("carry this on", hops=0).allowed
    assert check("carry this on", hops=1).allowed
    verdict = check("carry this on", hops=2)
    assert not verdict.allowed and verdict.guard == "loop"
    assert "would not stop on their own" in verdict.reason


def test_a_trusted_instance_may_be_sent_a_message_that_has_already_been_relayed():
    verdict = GuardChain([LoopGuard()]).check("on you go", {"hops": 9}, TRUSTED)
    assert verdict.allowed


def test_a_nonsense_hop_count_is_refused_rather_than_treated_as_zero():
    assert not check("hi", hops="lots").allowed
    assert not check("hi", hops=-1).allowed


def test_a_message_is_never_delivered_back_to_the_instance_it_came_from():
    verdict = check("loop me", origin="BATI")
    assert not verdict.allowed and verdict.guard == "self_talk"
    assert "Name a different instance" in verdict.reason
    assert check("fine", origin="someone_else").allowed


def test_the_chain_is_itself_a_guard_so_a_chain_can_hold_a_chain():
    inner = GuardChain([CredentialGuard(), LengthGuard(10)])
    outer = GuardChain([NonEmptyGuard(), inner, SelfTalkGuard()])
    assert outer.check("short", {}, BATI).allowed
    assert outer.check("much too long for ten", {}, BATI).guard == "length"
    assert outer.check("", {}, BATI).guard == "non_empty"


def test_an_ordinary_sentence_is_not_mistaken_for_a_secret():
    """`pass` on its own matches "passing this along", and a relay that refuses ordinary sentences as
    credentials teaches a caller to stop believing its refusals."""
    for message in ("keep passing this along", "the bill passed", "pin the tab", "I'll take a pass on that",
                    "tokenomics is not a word I like", "Fralick's Addition, Kent County"):
        assert check(message).allowed, message


def test_the_shapes_a_real_credential_arrives_in_are_still_caught():
    for message in ("passcode 1234", "passwords go in the vault", "my OTP is 338211",
                    "the one-time code is 1234", "TOTP seed", "x-api-key: abc", "CVV 441", "my SSN is ..."):
        assert not check(message).allowed, message
