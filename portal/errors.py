class PortalError(Exception):
    """A frozen portal request failed."""


class InvalidRecord(PortalError, ValueError):
    """An export or original row has invalid input fields."""


class DuplicateExport(PortalError):
    """The requested export identity has already been frozen."""


class UnknownExport(PortalError, LookupError):
    """The portal has no frozen export for the requested identity."""


class InvalidColumn(PortalError, ValueError):
    """The requested column does not exist on this portal."""


class PageOutOfRange(PortalError, LookupError):
    """The page number lies outside the selected frozen column."""


class SourceUnavailable(PortalError):
    """The portal is closed or the selected page cannot be read."""
