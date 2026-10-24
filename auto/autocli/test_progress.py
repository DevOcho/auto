"""Tests for auto.autocli.progress (docker build/push progress bars + ETA).

The stream parsers and timing cache are module private, but they are the
part worth pinning down, so this module reaches into them deliberately.
"""

# pylint: disable=protected-access

import json
from unittest.mock import MagicMock, patch

from autocli import progress

# ---------------------------------------------------------------------------
# Vertex name parsing
# ---------------------------------------------------------------------------


def test_step_name_re_matches_buildkit_variants():
    """Plain, multi-stage, and multi-platform step vertexes all parse."""
    cases = {
        "[3/9] RUN pip install -r requirements.txt": ("", "3", "9"),
        "[builder 3/9] COPY . .": ("builder", "3", "9"),
        "[linux/amd64 builder 2/5] RUN make": ("linux/amd64 builder", "2", "5"),
    }
    for name, (stage, idx, total) in cases.items():
        match = progress.STEP_NAME_RE.match(name)
        assert match, f"should have matched: {name}"
        assert match.group("stage") == stage
        assert match.group("idx") == idx
        assert match.group("total") == total


def test_step_name_re_ignores_internal_vertexes():
    """Buildkit's internal bookkeeping vertexes must not count as build steps."""
    for name in (
        "[internal] load build definition from Dockerfile",
        "[internal] load .dockerignore",
        "[auth] library/alpine:pull token for registry-1.docker.io",
        "exporting to image",
    ):
        assert progress.STEP_NAME_RE.match(name) is None


def test_layer_regexes():
    """docker push/pull layer chatter is classified into start vs done."""
    assert progress.LAYER_START_RE.match("96d82222afe3: Preparing")
    assert progress.LAYER_START_RE.match("96d82222afe3: Pulling fs layer")
    assert progress.LAYER_DONE_RE.match("96d82222afe3: Pushed")
    assert progress.LAYER_DONE_RE.match("96d82222afe3: Layer already exists")
    assert progress.LAYER_DONE_RE.match("96d82222afe3: Mounted from library/alpine")
    # A layer mid-transfer is neither started-only nor done
    assert (
        progress.LAYER_DONE_RE.match("96d82222afe3: Pushing [===>]  2.5MB/8MB") is None
    )


# ---------------------------------------------------------------------------
# Timing history / ETA
# ---------------------------------------------------------------------------


def _timeline(tmp_path, pod="kirkland", history=None):
    """Build a BuildTimeline backed by a throwaway history file."""
    path = tmp_path / "build_times.json"
    if history is not None:
        path.write_text(json.dumps(history), encoding="utf-8")
    with patch.object(progress, "HISTORY_PATH", str(path)):
        return progress.BuildTimeline(pod), str(path)


def test_timeline_without_history_gives_no_eta(tmp_path):
    """First ever build of a pod must not invent an estimate."""
    timeline, _ = _timeline(tmp_path)
    assert timeline.remaining(set()) is None


def test_timeline_sums_remaining_step_medians(tmp_path):
    """ETA is the sum of per-step medians for steps that haven't finished."""
    history = {
        "pods": {
            "kirkland": {
                "steps": {"[1/3] FROM python": [2.0], "[2/3] RUN pip install": [60.0]},
                "order": ["[1/3] FROM python", "[2/3] RUN pip install"],
                "total_steps": 3,
            }
        },
        "layers": {},
    }
    timeline, _ = _timeline(tmp_path, history=history)

    assert timeline.remaining(set()) == 62.0
    # Clearing the cheap first step leaves the expensive one still ahead
    assert timeline.remaining({"[1/3] FROM python"}) == 60.0
    # Getting through the slow step collapses the estimate, which is what lets
    # a cached build correct itself instead of promising a cold-build runtime
    assert timeline.remaining({"[1/3] FROM python", "[2/3] RUN pip install"}) == 0.0


def test_timeline_declines_to_guess_when_dockerfile_changed(tmp_path):
    """If most remaining steps are unrecognised, show nothing rather than a lie."""
    history = {
        "pods": {
            "kirkland": {
                "steps": {"[1/4] FROM python": [2.0]},
                "order": ["[1/4] FROM python", "new-a", "new-b", "new-c"],
                "total_steps": 4,
            }
        },
        "layers": {},
    }
    timeline, _ = _timeline(tmp_path, history=history)

    # 3 of the 4 steps have no samples of their own. The global median fallback
    # only comes from one recorded step, so it isn't worth trusting.
    assert timeline.remaining({"[1/4] FROM python"}) is None


def test_eta_survives_an_edited_dockerfile_line(tmp_path):
    """A renamed step must not stay 'pending' and hold the ETA above zero.

    Step names embed the command text, so editing a Dockerfile line renames
    that step. Looking the remaining work up by name would leave the replaced
    name unfinished forever, sticking its time in the estimate.
    """
    history = {
        "pods": {
            "kirkland": {
                "steps": {
                    "[1/3] FROM python": [2.0],
                    "[2/3] RUN old-command": [30.0],
                    "[3/3] COPY . .": [1.0],
                },
                "order": [
                    "[1/3] FROM python",
                    "[2/3] RUN old-command",
                    "[3/3] COPY . .",
                ],
                "total_steps": 3,
            }
        },
        "layers": {},
    }
    timeline, _ = _timeline(tmp_path, history=history)

    # This run ran the renamed step instead, then finished all 3 of its steps
    finished = {"[1/3] FROM python", "[2/3] RUN new-command", "[3/3] COPY . ."}
    assert timeline.remaining(finished) == 0.0

    # Mid-build, the renamed step is still charged its old cost -- the estimate
    # stays useful because a rename usually means similar work, not no work
    assert timeline.remaining({"[1/3] FROM python"}) == 31.0


def test_out_of_order_completion_keeps_pending_steps(tmp_path):
    """Parallel stages finish out of order; real pending work must still count."""
    history = {
        "pods": {
            "kirkland": {
                "steps": {
                    "[builder 1/2] FROM golang": [5.0],
                    "[builder 2/2] RUN go build": [40.0],
                    "[stage-1 1/2] FROM alpine": [2.0],
                    "[stage-1 2/2] COPY --from=builder": [1.0],
                },
                "order": [
                    "[stage-1 1/2] FROM alpine",
                    "[builder 1/2] FROM golang",
                    "[builder 2/2] RUN go build",
                    "[stage-1 2/2] COPY --from=builder",
                ],
                "total_steps": 4,
            }
        },
        "layers": {},
    }
    timeline, _ = _timeline(tmp_path, history=history)

    # The second stage's FROM finished first; the slow go build is still ahead
    # and must not be dropped as though it were behind us
    assert timeline.remaining({"[stage-1 1/2] FROM alpine"}) == 46.0


def test_timeline_round_trips_through_the_cache(tmp_path):
    """Timings recorded during a run are readable by the next run."""
    timeline, path = _timeline(tmp_path)
    timeline.record("[1/2] FROM python", 1.5)
    timeline.record("[2/2] RUN make", 30.0)

    with patch.object(progress, "HISTORY_PATH", path):
        timeline.save(total_steps=2)
        reloaded = progress.BuildTimeline("kirkland")

    assert reloaded.expected == ["[1/2] FROM python", "[2/2] RUN make"]
    assert reloaded.expected_total == 2
    assert reloaded.remaining(set()) == 31.5


def test_history_survives_a_corrupt_cache_file(tmp_path):
    """A mangled cache must degrade to 'no estimate', never crash the build."""
    path = tmp_path / "build_times.json"
    path.write_text("{not json at all", encoding="utf-8")

    with patch.object(progress, "HISTORY_PATH", str(path)):
        history = progress.load_history()

    assert history == {"pods": {}, "layers": {}}


def test_append_sample_keeps_only_recent_values():
    """Samples are capped so one slow cold build doesn't skew the median forever."""
    samples = {}
    for index in range(progress.MAX_SAMPLES + 3):
        progress._append_sample(samples, "step", float(index))

    assert len(samples["step"]) == progress.MAX_SAMPLES
    assert samples["step"][-1] == float(progress.MAX_SAMPLES + 2)


# ---------------------------------------------------------------------------
# Stream parsing -> tracker state
# ---------------------------------------------------------------------------


def _tracker(tmp_path):
    """A BuildTracker wired to a mock progress bar and empty history."""
    timeline, _ = _timeline(tmp_path)
    return progress.BuildTracker(timeline, MagicMock(), task_id=1)


def test_rawjson_stream_counts_steps(tmp_path):
    """rawjson vertexes advance the bar and set the total from '[k/N]'."""
    tracker = _tracker(tmp_path)
    lines = [
        json.dumps(
            {"vertexes": [{"digest": "a", "name": "[internal] load .dockerignore"}]}
        ),
        json.dumps({"vertexes": [{"digest": "b", "name": "[1/3] FROM python:3.12"}]}),
        json.dumps({"vertexes": [{"digest": "c", "name": "[2/3] COPY . ."}]}),
        json.dumps(
            {
                "vertexes": [
                    {"digest": "b", "name": "[1/3] FROM python:3.12", "completed": "t"}
                ]
            }
        ),
        "not json at all",
    ]
    for line in lines:
        progress._feed_rawjson(tracker, line)

    # The internal vertex was ignored; two real steps registered, one finished
    assert tracker.total == 3
    assert len(tracker.steps) == 2
    assert tracker.finished == {"[1/3] FROM python:3.12"}


def test_rawjson_marks_cached_steps_done_immediately(tmp_path):
    """A cached step counts as complete the moment buildkit reports it."""
    tracker = _tracker(tmp_path)
    progress._feed_rawjson(
        tracker,
        json.dumps(
            {
                "vertexes": [
                    {"digest": "b", "name": "[2/4] RUN pip install", "cached": True}
                ]
            }
        ),
    )
    assert tracker.finished == {"[2/4] RUN pip install"}


def test_rawjson_times_steps_from_buildkit_timestamps(tmp_path):
    """Step duration comes from buildkit, not from when we first heard about it.

    Buildkit announces a vertex well before it starts running. Timing from
    first sight counts queue time as work, which inflated a real 18s build's
    estimate to 45s.
    """
    tracker = _tracker(tmp_path)
    vertex = {
        "digest": "b",
        "name": "[2/3] RUN pip install",
        "started": "2026-08-01T20:03:24.690209954Z",
        "completed": "2026-08-01T20:03:30.190209954Z",
    }
    progress._feed_rawjson(tracker, json.dumps({"vertexes": [vertex]}))

    assert tracker.timeline.actuals["[2/3] RUN pip install"] == 5.5


def test_vertex_duration_without_timestamps_is_unknown():
    """A vertex that hasn't reported both ends yields no timing to record."""
    assert progress._vertex_duration({"started": "2026-08-01T20:03:24.6Z"}) is None
    assert progress._vertex_duration({}) is None


def test_plain_stream_uses_the_builders_own_timing(tmp_path):
    """'DONE 1.4s' is exact; CACHED means the step did no work."""
    tracker = _tracker(tmp_path)
    progress._feed_plain(tracker, "#8 [1/2] FROM python:3.12")
    progress._feed_plain(tracker, "#8 DONE 1.4s")
    progress._feed_plain(tracker, "#9 [2/2] RUN make")
    progress._feed_plain(tracker, "#9 CACHED")

    assert tracker.timeline.actuals["[1/2] FROM python:3.12"] == 1.4
    assert tracker.timeline.actuals["[2/2] RUN make"] == 0.0


def test_rawjson_tracks_the_export_tail(tmp_path):
    """'exporting to image' is most of a warm build, so it counts as a step."""
    tracker = _tracker(tmp_path)
    progress._feed_rawjson(
        tracker,
        json.dumps({"vertexes": [{"digest": "z", "name": "exporting to image"}]}),
    )
    assert tracker.steps["z"][0] == progress.EXPORT_STEP


def test_plain_stream_counts_steps(tmp_path):
    """--progress=plain output advances the bar for older buildx."""
    tracker = _tracker(tmp_path)
    for line in (
        "#5 [internal] load build context",
        "#8 [1/2] FROM python:3.12",
        "#8 DONE 0.4s",
        "#9 [2/2] RUN make",
        "#9 CACHED",
    ):
        progress._feed_plain(tracker, line)

    assert tracker.total == 2
    assert tracker.finished == {"[1/2] FROM python:3.12", "[2/2] RUN make"}


def test_classic_builder_stream_counts_steps(tmp_path):
    """The non-buildkit builder implies completion by starting the next step."""
    tracker = _tracker(tmp_path)
    for line in (
        "Step 1/2 : FROM python:3.12",
        " ---> Using cache",
        "Step 2/2 : RUN make",
    ):
        progress._feed_plain(tracker, line)

    # Step 1 closed when step 2 started; step 2 closes when the build succeeds
    assert tracker.finished == {"[1/2] FROM python:3.12"}
    tracker.finish()
    assert len(tracker.finished) == 2


def test_layer_tracker_counts_push_layers():
    """Push layers are the denominator; docker gives no reliable byte counts."""
    tracker = progress.LayerTracker(MagicMock(), task_id=1, predicted=None, started=0.0)
    for line in (
        "The push refers to repository [k3d-registry.local:12345/kirkland]",
        "96d82222afe3: Preparing",
        "1217d906a0b2: Preparing",
        "0b44b2151d78: Preparing",
        "1217d906a0b2: Pushed",
        "96d82222afe3: Layer already exists",
    ):
        tracker.feed(line)

    assert len(tracker.layers) == 3
    assert len(tracker.done) == 2

    # Docker doesn't always report the final layer, so success closes them out
    tracker.finish()
    assert len(tracker.done) == 3


# ---------------------------------------------------------------------------
# Builder detection and command construction
# ---------------------------------------------------------------------------


def test_builder_mode_detection():
    """buildx >= 0.13 gets rawjson, older gets plain, missing gets classic."""
    cases = {
        "github.com/docker/buildx v0.36.0 df28b0a": "rawjson",
        "github.com/docker/buildx v0.13.0 abc1234": "rawjson",
        "github.com/docker/buildx v0.10.4 abc1234": "plain",
        "": "classic",
    }
    for output, expected in cases.items():
        progress._CACHE.clear()
        with patch.object(progress.utils, "run_and_return", return_value=output):
            assert progress.builder_mode() == expected
    progress._CACHE.clear()


@patch.object(progress, "get_console")
@patch.object(progress.utils, "run_and_wait", return_value=1)
def test_build_falls_back_to_quiet_run_when_not_a_terminal(mock_run, mock_console):
    """Piped output (CI, redirect) keeps the original non-animated behaviour."""
    mock_console.return_value.is_terminal = False

    with patch.object(progress, "builder_mode", return_value="rawjson"):
        assert progress.build_image("kirkland", "0.1.0", "/code/kirkland") == 1

    mock_run.assert_called_once_with(
        "docker build --progress=rawjson -t kirkland:0.1.0 /code/kirkland"
    )


@patch.object(progress, "get_console")
@patch.object(progress.utils, "run_and_wait", return_value=1)
def test_build_omits_progress_flag_for_classic_builder(mock_run, mock_console):
    """Without buildx, --progress isn't a valid flag and would break the build."""
    mock_console.return_value.is_terminal = False

    with patch.object(progress, "builder_mode", return_value="classic"):
        progress.build_image("kirkland", "0.1.0", "/code/kirkland")

    mock_run.assert_called_once_with("docker build -t kirkland:0.1.0 /code/kirkland")


def test_format_duration():
    """Durations render as m:ss, rolling over to h:mm:ss."""
    assert progress.format_duration(0) == "0:00"
    assert progress.format_duration(72) == "1:12"
    assert progress.format_duration(3661) == "1:01:01"
    assert progress.format_duration(-5) == "0:00"
