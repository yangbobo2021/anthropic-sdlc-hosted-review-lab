"""A synthetic readiness endpoint for the review-rule acceptance."""


def readiness():
    """Return the HTTP status and text consumed by the readiness checker."""
    return 200, "ready"
