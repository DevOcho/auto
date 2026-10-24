"""Streaming progress bars for the long-running docker steps in `auto tag`

`utils.run_and_wait()` buffers a command's output until it exits, which makes a
cold `docker build` look like a frozen terminal for minutes at a time. The
helpers here stream the command instead, translate its output into step and
layer counts, and render a rich progress bar.

The ETA comes from how long each step took on previous runs (cached in
~/.auto/cache/build_times.json) rather than from linear extrapolation, because
build steps are wildly non-uniform -- a cached COPY is instant while a
`RUN pip install` is minutes. Steps that buildkit reports as cached drop out of
the estimate the moment we see them, so a warm build corrects its ETA within the
first second instead of promising a cold-build runtime.
"""

import json
import os
import re
import time
from datetime import datetime
from statistics import median

from autocli import utils
from rich import get_console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column
from rich.text import Text

# Where we remember how long each build step took last time
HISTORY_PATH = os.path.expanduser("~/.auto/cache/build_times.json")

# Keep a handful of samples per step so one cold outlier doesn't skew the
# median forever, but enough that a single fluke doesn't either
MAX_SAMPLES = 5

# Buildkit's "exporting to image" tail work isn't a numbered step, but on a
# fully cached build it's most of the runtime, so we track it as a pseudo step
EXPORT_PREFIX = "exporting to image"
EXPORT_STEP = "__export__"

# Buildkit gained --progress=rawjson in buildx v0.13
RAWJSON_MIN_VERSION = (0, 13)

# Display sizing.  These matter: rich responds to a too-wide layout by ellipsing
# every column at once, which turns the bar into an unreadable row of dots on a
# standard 80 column terminal.
BAR_WIDTH = 20
NARROW_BAR_WIDTH = 10
WIDE_TERMINAL = 100

# Roughly what the spinner, counts, elapsed and ETA columns cost around the bar
FIXED_COLUMNS_WIDTH = 30

# Don't bother showing the current step unless there's room to say something
MIN_STEP_WIDTH = 12
DESC_WIDTH = 44

# Matches a buildkit step vertex: "[3/9] RUN ...", "[builder 3/9] RUN ...",
# "[linux/amd64 builder 3/9] RUN ...". Non-step vertexes ("[internal] load
# build definition", "[auth] ... token") deliberately do not match.
STEP_NAME_RE = re.compile(
    r"^\[(?P<stage>.*?)\s*(?P<idx>\d+)/(?P<total>\d+)\]\s*(?P<desc>.*)$"
)

# --progress=plain output: "#8 [4/5] RUN echo" then "#8 DONE 0.4s" / "#8 CACHED"
PLAIN_VERTEX_RE = re.compile(r"^#(?P<id>\d+)\s+(?P<name>\[.+)$")
PLAIN_DONE_RE = re.compile(r"^#(?P<id>\d+)\s+(?:DONE\s+(?P<secs>[\d.]+)s|CACHED)\b")

# Classic (non-buildkit) builder: "Step 4/5 : RUN echo"
CLASSIC_STEP_RE = re.compile(r"^Step\s+(?P<idx>\d+)/(?P<total>\d+)\s*:\s*(?P<desc>.*)$")

# docker push/pull layer chatter. Non-TTY docker gives us layer state changes
# but no reliable byte counts, so layers are the denominator we can trust.
LAYER_START_RE = re.compile(
    r"^(?P<id>[0-9a-f]{8,}):\s+(?:Preparing|Waiting|Pulling fs layer)\s*$"
)
LAYER_DONE_RE = re.compile(
    r"^(?P<id>[0-9a-f]{8,}):\s+"
    r"(?:Pushed|Pull complete|Layer already exists|Already exists|Mounted from\b.*)\s*$"
)

# Cache for probes we only want to run once per process
_CACHE = {}


def format_duration(seconds) -> str:
    """Render a number of seconds as m:ss (or h:mm:ss) for the progress columns"""

    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _parse_timestamp(value):
    """Parse one of buildkit's RFC3339 timestamps into a float of seconds

    Buildkit emits nanosecond precision ("...T20:03:24.690209954Z") which
    fromisoformat won't take, so the fraction is trimmed to microseconds. The
    result is only ever used as a difference between two timestamps parsed the
    same way, so the naive/local interpretation cancels out.
    """

    if not value:
        return None

    text = str(value).rstrip("Z")
    if "." in text:
        head, fraction = text.split(".", 1)
        text = f"{head}.{fraction[:6]}"

    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _vertex_duration(vertex):
    """How long a buildkit vertex actually ran, or None if it can't be derived

    We can't time this ourselves: buildkit announces a vertex well before it
    starts running, so measuring from when we first hear about it counts queue
    time as work and badly over-estimates every step.
    """

    started = _parse_timestamp(vertex.get("started"))
    completed = _parse_timestamp(vertex.get("completed"))

    if started is None or completed is None:
        return None
    return max(0.0, completed - started)


def _shorten(text: str, limit: int = DESC_WIDTH) -> str:
    """Collapse whitespace and truncate so the bar stays on one line"""

    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class _EtaColumn(ProgressColumn):
    """Show the history-based ETA, or a dash when we have nothing to go on"""

    def render(self, task):
        eta = task.fields.get("eta")
        if eta is None:
            return Text("~ --:--", style="dim")
        return Text(f"~{format_duration(eta)} left", style="cyan")


def _make_progress(console, label_width):
    """Build the shared progress bar layout used by build, push, and pull

    Columns are sized against the real terminal width. An 80 column terminal
    can't fit a wide bar *and* the current step, and rich reacts to overflow by
    ellipsing every column at once, so we drop the step column instead of
    letting the whole row collapse into dots.
    """

    bar_width = BAR_WIDTH if console.width >= WIDE_TERMINAL else NARROW_BAR_WIDTH

    columns = [
        SpinnerColumn(style="bright_cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(
            bar_width=bar_width, complete_style="bright_cyan", finished_style="green"
        ),
        TextColumn("[bold]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        _EtaColumn(),
    ]

    room = console.width - (label_width + bar_width + FIXED_COLUMNS_WIDTH)
    if room >= MIN_STEP_WIDTH:
        columns.append(
            TextColumn(
                "[dim]{task.fields[step]}",
                table_column=Column(
                    no_wrap=True, overflow="ellipsis", max_width=min(room, DESC_WIDTH)
                ),
            )
        )

    return Progress(*columns, console=console)


def load_history() -> dict:
    """Read the cached timings, tolerating a missing or corrupt cache file"""

    data = {}
    try:
        with open(HISTORY_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("pods", {})
    data.setdefault("layers", {})
    return data


def save_history(history: dict) -> None:
    """Persist the timing cache. Failures are ignored -- it's only an estimate"""

    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as handle:
            json.dump(history, handle, indent=2)
    except OSError:
        pass


def _append_sample(samples: dict, key: str, seconds: float) -> None:
    """Record a duration, keeping only the most recent MAX_SAMPLES of them"""

    values = samples.setdefault(key, [])
    values.append(round(float(seconds), 2))
    del values[:-MAX_SAMPLES]


class BuildTimeline:
    """Predicts and records how long each build step takes for a given pod

    Predictions are per step name rather than a whole-build average because
    build times are bimodal: the same pod takes seconds when every layer is
    cached and minutes when nothing is. Summing per-step medians for the steps
    that haven't finished yet handles both cases with one model.
    """

    def __init__(self, pod):
        self.pod = pod
        self._history = load_history()

        record = self._history["pods"].get(pod) or {}
        self.samples = record.get("steps") or {}
        self.expected = record.get("order") or []
        self.expected_total = record.get("total_steps") or 0

        self.actuals = {}
        self.order = []

    def _global_median(self):
        """Fallback guess for a step we've never timed (e.g. a new Dockerfile line)"""

        pooled = [median(values) for values in self.samples.values() if values]
        return median(pooled) if pooled else None

    def remaining(self, finished):
        """Estimated seconds left given the set of step names already done

        The steps still ahead are taken by position in the recorded order --
        the ones past however many have finished -- rather than by matching
        names. Names are unreliable across runs: a step's name embeds its
        command, so editing any Dockerfile line renames that step, and a name
        lookup would leave the step it replaced "pending" forever with its
        time stuck in the estimate. Position also survives the out-of-order
        completion that parallel stages in a multi-stage build produce, since
        what matters is how much work is left, not which name finished first.

        Returns None when we can't make an honest guess -- no history for this
        pod, or the Dockerfile changed enough that most steps are unrecognised.
        """

        if not self.expected:
            return None

        done = len(finished)
        pending = self.expected[done:]
        if not pending:
            return 0.0

        fallback = self._global_median()
        estimate = 0.0
        measured = 0

        for name in pending:
            values = self.samples.get(name)
            if values:
                estimate += median(values)
                measured += 1
            elif fallback is not None:
                # A step we've never timed (new Dockerfile line) still has to
                # cost something, so stand in the typical step duration
                estimate += fallback

        # If we're standing in a guess for more than half the remaining steps,
        # showing a number would be worse than showing nothing
        if measured * 2 < len(pending):
            return None

        return estimate

    def record(self, name, seconds):
        """Remember how long a step took during this run"""

        if name in self.actuals:
            return
        self.actuals[name] = seconds
        self.order.append(name)

    def save(self, total_steps):
        """Fold this run's timings into the cache for the next build"""

        for name, seconds in self.actuals.items():
            _append_sample(self.samples, name, seconds)

        self._history["pods"][self.pod] = {
            "steps": self.samples,
            "order": self.order,
            "total_steps": total_steps,
        }
        save_history(self._history)


class BuildTracker:
    """Turns builder step events into progress bar updates"""

    def __init__(self, timeline, progress, task_id):
        self.timeline = timeline
        self.progress = progress
        self.task_id = task_id

        self.stage_totals = {}  # stage name -> that stage's step count
        self.steps = {}  # vertex key -> (timing history name, monotonic start)
        self.finished = set()  # step names completed this run
        self.last_key = None

    @property
    def total(self):
        """Denominator for the bar

        Buildkit reveals multi-stage builds lazily, so this grows as new stages
        appear. Before any step arrives we fall back to last run's count so the
        bar starts at a sensible width instead of jumping.
        """

        live = sum(self.stage_totals.values())
        if live:
            return max(live, len(self.finished))
        return max(self.timeline.expected_total, 1)

    def _classify(self, name):
        """Work out the (stage, step count, history key, label) for a vertex name"""

        match = STEP_NAME_RE.match(name)
        if match:
            return (
                match.group("stage"),
                int(match.group("total")),
                name,
                match.group("desc"),
            )

        if name.startswith(EXPORT_PREFIX):
            # Stable key so the export tail is predictable across runs even
            # though its vertex name carries no step numbers
            return EXPORT_STEP, 1, EXPORT_STEP, "exporting image"

        return None

    def register(self, key, name):
        """Record a step the first time the builder mentions it"""

        if key in self.steps:
            return

        classified = self._classify(name)
        if classified is None:
            return

        stage, count, history_key, label = classified
        self.steps[key] = (history_key, time.monotonic())
        self.stage_totals[stage] = count
        self.last_key = key
        self.refresh(label)

    def complete(self, key, seconds=None):
        """Mark a step done and fold its duration into the timeline

        ``seconds`` is the builder's own measurement where it gives us one.
        Falling back to our own clock over-counts, because it starts ticking
        when the step was announced rather than when it began running.
        """

        entry = self.steps.get(key)
        if entry is None:
            return

        name, first_seen = entry
        if name in self.finished:
            return

        if seconds is None:
            seconds = time.monotonic() - first_seen

        self.finished.add(name)
        self.timeline.record(name, seconds)
        self.refresh()

    def complete_previous(self):
        """Close out the previous step (classic builder implies it by starting the next)"""

        if self.last_key is not None:
            self.complete(self.last_key)

    def finish(self):
        """Close out anything still open once the build has succeeded"""

        for key in list(self.steps):
            self.complete(key)

    def refresh(self, step=None):
        """Push current counts and ETA into the bar"""

        fields = {"eta": self.timeline.remaining(self.finished)}
        if step is not None:
            fields["step"] = _shorten(step)

        self.progress.update(
            self.task_id,
            completed=len(self.finished),
            total=self.total,
            **fields,
        )


class LayerTracker:
    """Turns docker push/pull layer chatter into progress bar updates"""

    def __init__(self, progress, task_id, predicted, started):
        self.progress = progress
        self.task_id = task_id
        self.predicted = predicted
        self.started = started
        self.layers = set()
        self.done = set()

    def feed(self, line):
        """Consume a single line of docker push/pull output"""

        match = LAYER_DONE_RE.match(line)
        if match:
            self.layers.add(match.group("id"))
            self.done.add(match.group("id"))
            self.refresh()
            return

        match = LAYER_START_RE.match(line)
        if match:
            self.layers.add(match.group("id"))
            self.refresh()

    def finish(self):
        """Docker doesn't always report the last layer, so close them all out"""

        self.done |= self.layers
        self.refresh()

    def refresh(self):
        """Push current counts and ETA into the bar"""

        eta = None
        if self.predicted is not None:
            eta = max(0.0, self.predicted - (time.monotonic() - self.started))

        self.progress.update(
            self.task_id,
            completed=len(self.done),
            total=max(len(self.layers), 1),
            eta=eta,
            step="layers",
        )


def _feed_rawjson(tracker, line):
    """Consume one NDJSON line of `docker build --progress=rawjson` output"""

    try:
        payload = json.loads(line)
    except ValueError:
        return

    for vertex in payload.get("vertexes") or []:
        name = vertex.get("name") or ""
        key = vertex.get("digest") or name
        tracker.register(key, name)

        # A cached step is done the instant we hear about it, which is what
        # lets a warm build's ETA collapse almost immediately
        if vertex.get("completed") or vertex.get("cached"):
            tracker.complete(key, seconds=_vertex_duration(vertex))


def _feed_plain(tracker, line):
    """Consume one line of `--progress=plain` or classic builder output"""

    match = PLAIN_VERTEX_RE.match(line)
    if match:
        tracker.register(match.group("id"), match.group("name"))
        return

    match = PLAIN_DONE_RE.match(line)
    if match:
        # "DONE 1.4s" carries the builder's own timing; "CACHED" means no work
        secs = match.group("secs")
        tracker.complete(match.group("id"), seconds=float(secs) if secs else 0.0)
        return

    match = CLASSIC_STEP_RE.match(line)
    if match:
        # The classic builder never says "done" -- starting the next step is
        # the only signal that the previous one finished
        tracker.complete_previous()
        name = f"[{match.group('idx')}/{match.group('total')}] {match.group('desc')}"
        tracker.register(match.group("idx"), name)


def builder_mode():
    """Decide which progress format `docker build` can give us

    Returns "rawjson" (buildx >= 0.13), "plain" (older buildx), or "classic"
    (no buildx at all, which means --progress isn't a valid flag).
    """

    if "builder" not in _CACHE:
        version = utils.run_and_return("docker buildx version")
        match = re.search(r"v(\d+)\.(\d+)", version)

        if not match:
            _CACHE["builder"] = "classic"
        elif (int(match.group(1)), int(match.group(2))) >= RAWJSON_MIN_VERSION:
            _CACHE["builder"] = "rawjson"
        else:
            _CACHE["builder"] = "plain"

    return _CACHE["builder"]


def build_image(pod, version, context_path) -> int:
    """Run `docker build` with a live step progress bar and ETA

    Returns 1 on success and 0 on failure so callers can treat this exactly
    like utils.run_and_wait().
    """

    mode = builder_mode()
    flag = "" if mode == "classic" else f" --progress={mode}"
    command = f"docker build{flag} -t {pod}:{version} {context_path}"

    # Piped or redirected output (CI, `auto tag foo > log`): a live bar would
    # just be thousands of redraw lines, so keep the original quiet behaviour
    console = get_console()
    if not console.is_terminal:
        return utils.run_and_wait(command)

    timeline = BuildTimeline(pod)
    feed = _feed_rawjson if mode == "rawjson" else _feed_plain
    label = f"Building {pod} {version}"

    with _make_progress(console, len(label)) as progress:
        task_id = progress.add_task(
            f"[bright_cyan]Building[/] {pod} {version}",
            total=max(timeline.expected_total, 1),
            completed=0,
            eta=timeline.remaining(set()),
            step="starting",
        )
        tracker = BuildTracker(timeline, progress, task_id)
        result = utils.run_and_stream(command, on_line=lambda line: feed(tracker, line))
        if result:
            tracker.finish()

    # Only learn from builds that actually finished, otherwise a build that
    # died halfway would teach us that every step is fast
    if result:
        timeline.save(tracker.total)

    return result


def _run_layer_command(command, label, image, suppress_error) -> int:
    """Run a docker push/pull with a per-layer progress bar"""

    console = get_console()
    if not console.is_terminal:
        return utils.run_and_wait(
            command, capture_output=True, suppress_error=suppress_error
        )

    history = load_history()
    key = f"{label.lower()}:{image}"
    samples = history["layers"].get(key) or []
    predicted = median(samples) if samples else None

    # Registry-qualified image names get long, so let the bar reclaim the space
    # the name doesn't need rather than pushing the whole row off the terminal
    title = f"{label} {_shorten(image)}"

    started = time.monotonic()
    with _make_progress(console, len(title)) as progress:
        task_id = progress.add_task(
            f"[bright_cyan]{label}[/] {_shorten(image)}",
            total=1,
            completed=0,
            eta=predicted,
            step="layers",
        )
        tracker = LayerTracker(progress, task_id, predicted, started)
        result = utils.run_and_stream(
            command, on_line=tracker.feed, suppress_error=suppress_error
        )
        if result:
            tracker.finish()

    if result:
        _append_sample(history["layers"], key, time.monotonic() - started)
        save_history(history)

    return result


def push_image(image, suppress_error=False) -> int:
    """Run `docker push` with a per-layer progress bar"""

    return _run_layer_command(f"docker push {image}", "Pushing", image, suppress_error)


def pull_image(image, suppress_error=False) -> int:
    """Run `docker pull` with a per-layer progress bar"""

    return _run_layer_command(f"docker pull {image}", "Pulling", image, suppress_error)
