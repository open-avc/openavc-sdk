"""The interface policy asks a plugin panel token's check the way the server
it runs on asks it: with the request's connection where the server checks
that the panel is still admitted, without it on an older server."""

from openavc_oem.interfaces import _panel_token_holds


def test_a_server_that_checks_admission_is_handed_the_connection():
    seen = []

    def verify(token, plugin_id, conn):
        seen.append((token, plugin_id, conn))
        return True

    assert _panel_token_holds(verify, "t", "vp", "conn")
    assert seen == [("t", "vp", "conn")]


def test_an_older_server_is_asked_with_the_token_and_plugin_only():
    def verify(token, plugin_id):
        return (token, plugin_id) == ("t", "vp")

    assert _panel_token_holds(verify, "t", "vp", "conn")
