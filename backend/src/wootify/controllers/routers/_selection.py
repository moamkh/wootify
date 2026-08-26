"""Helpers for selecting the legacy endpoint registrations by API area."""

from collections.abc import Callable

from fastapi.routing import APIRoute

from wootify.controllers import _api_v1_impl


def select_routes(predicate: Callable[[APIRoute], bool]) -> list[APIRoute]:
    """Return the legacy routes matching ``predicate`` in declaration order."""

    return [route for route in _api_v1_impl.router.routes if isinstance(route, APIRoute) and predicate(route)]


def path_starts_with(*prefixes: str) -> Callable[[APIRoute], bool]:
    """Build a predicate matching route paths under one of ``prefixes``."""

    return lambda route: route.path.startswith(prefixes)


def endpoint_named(*names: str) -> Callable[[APIRoute], bool]:
    """Build a predicate matching the named endpoint functions."""

    return lambda route: route.name in names
