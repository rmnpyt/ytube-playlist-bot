"""Tests, and the fakes they share.

A package rather than a loose folder because the fakes are imported as
``tests.fakes``: they are deliberately reusable across test modules, and mypy needs the
same unambiguous module name that pytest does.
"""
