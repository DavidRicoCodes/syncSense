"""Non-connectable SSH boundary for the safe first increment."""

from __future__ import annotations

from ..domain import CapabilityDisabled
from .fake import FakeProcessAdapter


class SshProcessAdapter:
    """Deliberately disabled until a later, explicitly authorized increment."""

    def __init__(self, *args, **kwargs):
        raise CapabilityDisabled("Real SSH process execution is disabled in this increment")


class FakeSshProcessAdapter(FakeProcessAdapter):
    """SSH-shaped deterministic test double that never opens a socket."""

