"""API router package.

Keep package initialization light so importing request/response models does not
pull in the full router graph and create import cycles in worker code.
"""

__all__ = []
