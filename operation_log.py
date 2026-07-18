"""Thread-local operation event routing for in-process recovery modules.

The recovery implementations can still be launched as standalone command-line tools,
where events go to the console.  The desktop application binds a callback instead, so
the same implementation reports directly to the GUI without stdout redirection or
parsing console text.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import builtins


_SINK = ContextVar("ms41_operation_log_sink", default=None)


@contextmanager
def operation_log_sink(sink):
    """Route operation events to ``sink(message, level)`` in the current thread."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def emit(*values, level="info", sep=" ", end="\n", **print_options):
    """Emit an application event, or behave like ``print`` outside the application."""
    sink = _SINK.get()
    if sink is None:
        builtins.print(*values, sep=sep, end=end, **print_options)
        return

    message = sep.join(str(value) for value in values)
    if end not in ("", "\n"):
        message += end
    for line in message.replace("\r", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sink(line, level)
        except TypeError:
            sink(line)


def event(message, level="info"):
    emit(message, level=level)
