"""Exceptions shared by the core and its adapters."""


class AudioError(Exception):
    """Base class for expected OpenAudioKit failures."""


class ValidationError(AudioError, ValueError):
    """Input cannot be represented or processed as requested."""


class AudioLimitError(ValidationError):
    """Input exceeds a configured resource limit."""


class AudioIOError(AudioError, OSError):
    """A recording or report could not be read or written."""
