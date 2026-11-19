"""Live integration checks for the AbletonMCP tools.

These talk to a RUNNING Ableton Live 12 session that has the AbletonMCP Remote
Script loaded (TCP localhost:9877). They are deselected by default
(pyproject addopts: -m 'not integration').

Run from the repo root, with Live open:

    python -m pytest tests/integration -m integration -v

Safety: every test that changes the set works on a scratch MIDI track that it
creates and removes again. Read-only checks touch nothing. The set's existing
tracks, returns and devices are never modified.
"""
import json
import math
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

pytestmark = pytest.mark.integration

UTILITY_URI = "query:AudioFx#Utility"
DB_READOUT = re.compile(r"Live shows (-?\d+(?:\.\d+)?|-inf) dB")


@pytest.fixture(scope="module")
def server():
    """The real MCP_Server.server module, or skip when Live is unreachable."""
    try:
        from MCP_Server import server as srv
    except Exception as exc:  # environment dependent
        pytest.skip("Cannot import MCP_Server.server: {0}".format(exc))
    try:
        srv.get_ableton_connection()
    except Exception as exc:
        pytest.skip(
            "No running Ableton with the AbletonMCP Remote Script: {0}".format(exc)
        )
    return srv


@pytest.fixture
def ctx():
    return MagicMock()


def _session(server, ctx):
    return json.loads(server.get_session_info(ctx))


@pytest.fixture
def scratch_track(server, ctx):
    """Create a scratch MIDI track at the end of the session, remove it afterwards.

    Yields its 1-based track number.
    """
    before = _session(server, ctx)["track_count"]
    created = server.create_midi_track(ctx, index=-1)
    match = re.search(r"Created new MIDI track: (.+)$", created)
    assert match, "unexpected create_midi_track reply: {0!r}".format(created)
    name = match.group(1).strip()
    number = before + 1
    try:
        info = json.loads(server.get_track_info(ctx, number))
        assert info["name"] == name
        yield number
    finally:
        # Only delete the track if it is still the one this fixture created.
        try:
            info = json.loads(server.get_track_info(ctx, number))
            if info.get("name") == name:
                server.delete_track(ctx, track_index=number)
        except Exception:
            pass
    assert _session(server, ctx)["track_count"] == before, "scratch track was not removed"


class TestTrackInfo:
    def test_return_tracks_resolve(self, server, ctx):
        session = _session(server, ctx)
        if session["return_track_count"] == 0:
            pytest.skip("Set has no return tracks")
        number = session["track_count"] + 1
        info = json.loads(server.get_track_info(ctx, number))
        assert info["is_return_track"] is True
        assert info["track_number"] == number
        assert info["clip_slots"] == []
        assert isinstance(info["devices"], list)

    def test_out_of_range_still_errors(self, server, ctx):
        session = _session(server, ctx)
        past_end = session["track_count"] + session["return_track_count"] + 1
        result = server.get_track_info(ctx, past_end)
        assert "out of range" in result.lower()

    def test_session_track_keeps_fields_and_gains_track_number(self, server, ctx):
        info = json.loads(server.get_track_info(ctx, 1))
        for key in (
            "index", "name", "is_audio_track", "is_midi_track", "is_group_track",
            "mute", "solo", "arm", "volume", "panning", "clip_slots", "devices",
        ):
            assert key in info, key
        assert info["track_number"] == 1
        assert info["index"] == 0
        assert not info.get("is_return_track")


class TestFaderReadout:
    def test_unity_reads_zero_db(self, server, ctx, scratch_track):
        result = server.set_track_volume(ctx, scratch_track, 0.85)
        assert "Live shows 0.0 dB" in result

    def test_readout_is_lives_not_a_linear_ratio(self, server, ctx, scratch_track):
        result = server.set_track_volume(ctx, scratch_track, 0.7)
        match = DB_READOUT.search(result)
        assert match, result
        # The old code printed 20*log10(value/0.85), about -1.7 dB at 0.7.
        old_linear_estimate = 20 * math.log10(0.7 / 0.85)
        assert abs(float(match.group(1)) - old_linear_estimate) > 0.5
        read_back = server.get_track_volume(ctx, scratch_track)
        assert match.group(0) in read_back


class TestDeviceTools:
    def test_load_message_names_the_new_device(self, server, ctx, scratch_track):
        result = server.load_instrument_or_effect(ctx, scratch_track, UTILITY_URI)
        assert "New devices: Utility" in result

    def test_parameter_tools_do_not_hit_the_import_bug(self, server, ctx, scratch_track):
        server.load_instrument_or_effect(ctx, scratch_track, UTILITY_URI)

        listing = server.get_device_parameters(
            ctx, track_index=scratch_track, device_index=1
        )
        assert "No module named" not in listing
        assert "Utility" in listing

        full = server.get_device_parameters(
            ctx, track_index=scratch_track, device_index=1, show_all=True
        )
        names = re.findall(r"^\s*\d+\.\s+(.+?):", full, re.MULTILINE)
        width = next((n for n in names if "Width" in n), None)
        assert width, "no width parameter found in {0}".format(names)

        reply = server.set_device_parameter(
            ctx, track_index=scratch_track, device_index=1,
            parameter_name=width, value=0.75,
        )
        assert "No module named" not in reply
        assert "Error" not in reply

        # The reply's display string can lag one write behind (seen on VST3),
        # so confirm the new value by re-reading instead.
        after = server.get_device_parameters(
            ctx, track_index=scratch_track, device_index=1, show_all=True
        )
        line = next(l for l in after.splitlines() if width in l)
        assert "(normalized 0.75)" in line
