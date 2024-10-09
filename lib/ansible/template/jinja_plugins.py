"""Jinja template plugins (filters, tests, lookups) and custom global functions."""

from __future__ import annotations

import collections.abc as c
import datetime
import functools
import typing as t

from ..errors import (
    AnsibleError,
    AnsibleTemplatePluginNotFoundError,
    AnsibleTemplatePluginError,
    AnsibleTemplatePluginLoadError,
    AnsibleTemplatePluginRuntimeError,
)

from ..module_utils.common.collections import is_sequence
from ..module_utils.datatag import AnsibleTagHelper
from ..utils.datatag.tags import TrustedAsTemplate
from ..plugins import AnsibleJinja2Plugin
from ..plugins.loader import lookup_loader, Jinja2Loader
from ..plugins.lookup import LookupBase
from ..utils.display import Display
from .datatag import _JinjaConstTemplate
from .jinja_common import DeferredMarkerError, _TemplateConfig, get_first_marker_arg, JinjaCallContext, DeferredMarker
from .lazy_containers import _ITERATOR_TYPES, proxy_kwargs, proxy_args, proxy_jinja_constant_container
from .utils import TemplateContext

_display = Display()


class JinjaPluginIntercept(c.MutableMapping):
    """
    Simulated dict class that loads Jinja2Plugins at request
    otherwise all plugins would need to be loaded a priori.

    NOTE: plugin_loader still loads all 'builtin/legacy' at
    start so only collection plugins are really at request.
    """

    def __init__(self, jinja_builtins: c.Mapping[str, t.Callable], plugin_loader: Jinja2Loader):
        super(JinjaPluginIntercept, self).__init__()

        self._plugin_loader = plugin_loader

        # Jinja's environment mapping of known names (initially just J2 builtins)
        self._jinja_builtins = jinja_builtins
        self._wrapped_funcs: dict[str, t.Callable] = {}

    def _wrap_and_set_func(self, name: str, plugin_func: t.Callable, accept_deferred_marker: bool) -> t.Callable:
        if self._plugin_loader.type == 'filter':
            plugin_func = self._wrap_filter(plugin_func, name, accept_deferred_marker=accept_deferred_marker)
        else:
            plugin_func = self._wrap_test(plugin_func, name, accept_deferred_marker=accept_deferred_marker)

        self._wrapped_funcs[name] = plugin_func

        return plugin_func

    def __getitem__(self, key: str) -> t.Callable:
        plugin_func: t.Callable[..., t.Any] | None

        if plugin_func := self._wrapped_funcs.get(key):
            return plugin_func

        plugin_load_ex: Exception | None = None
        accept_deferred_marker = False

        try:
            plugin: AnsibleJinja2Plugin | None = self._plugin_loader.get(key)
        except KeyError:
            # The plugin name was invalid or no plugin was found by that name.
            pass
        except AnsibleError as ex:
            # The plugin was found, but an error occurred while trying to load the plugin.
            plugin_load_ex = ex
        except Exception as ex:
            # An unexpected exception occurred.
            raise AnsibleTemplatePluginLoadError(self._plugin_loader.type, key) from ex
        else:
            if plugin:
                # A missing filter/test can result in `plugin` being `None` instead of a `KeyError` being raised.
                plugin_func = plugin.j2_function
                accept_deferred_marker = plugin.accept_deferred_marker

        if not plugin_func:
            try:
                plugin_func = self._jinja_builtins[key]
            except KeyError:
                if plugin_load_ex:
                    raise AnsibleTemplatePluginLoadError(self._plugin_loader.type, key) from plugin_load_ex

                raise AnsibleTemplatePluginNotFoundError(self._plugin_loader.type, key) from None

        plugin_func = self._wrap_and_set_func(key, plugin_func, accept_deferred_marker)

        return plugin_func

    def __setitem__(self, key: str, value: t.Callable) -> None:
        self._wrap_and_set_func(key, value, accept_deferred_marker=False)

    def __delitem__(self, key):
        raise NotImplementedError()

    def __contains__(self, item: t.Any) -> bool:
        try:
            self.__getitem__(item)
        except AnsibleTemplatePluginLoadError:
            return True
        except AnsibleTemplatePluginNotFoundError:
            return False

        return True

    def __iter__(self):
        raise NotImplementedError()  # dynamic container

    def __len__(self):
        raise NotImplementedError()  # dynamic container

    @staticmethod
    def _wrap_test(func: t.Callable, plugin_name: str, accept_deferred_marker: bool) -> t.Callable:
        """Intercept point for all test plugins to ensure that args are properly templated/lazified."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> bool | DeferredMarker:
            # DTFIX-MERGE: FDI050 consider replacing this and the nested behavior with split decorators?
            if not accept_deferred_marker:
                if (first_marker := get_first_marker_arg(args, kwargs)) is not None:
                    return first_marker

            try:
                with JinjaCallContext(eager_trip_deferred_marker=not accept_deferred_marker):
                    test_res = func(*proxy_args(args), **proxy_kwargs(kwargs))
            except DeferredMarkerError as ex:
                return ex.source
            except Exception as ex:
                raise AnsibleTemplatePluginRuntimeError('test', plugin_name) from ex

            if not isinstance(test_res, bool):
                template = TemplateContext.current().template_value

                _display.deprecated(
                    msg=f"The test plugin {plugin_name!r} returned a non-boolean result of type {type(test_res)!r}. "
                        "Test plugins must have a boolean result.",
                    obj=template,
                    version="2.21",
                )

                test_res = bool(test_res)

            return test_res

        return wrapper

    @staticmethod
    def _wrap_filter(func: t.Callable, plugin_name: str, accept_deferred_marker: bool) -> t.Callable:
        """Intercept point for all filter plugins to ensure that args are properly templated/lazified."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> t.Any:
            # DTFIX-MERGE: FDI050 consider replacing this and the nested behavior with split decorators?
            if not accept_deferred_marker:
                if (first_marker := get_first_marker_arg(args, kwargs)) is not None:
                    return first_marker

            try:
                with JinjaCallContext(eager_trip_deferred_marker=not accept_deferred_marker):
                    return _wrap_plugin_output(func(*proxy_args(args), **proxy_kwargs(kwargs)))
            except DeferredMarkerError as ex:
                return ex.source
            except Exception as ex:
                raise AnsibleTemplatePluginRuntimeError('filter', plugin_name) from ex

        return wrapper


_TCallable = t.TypeVar("_TCallable", bound=t.Callable)


class _DirectCall:
    """Functions/methods marked `_DirectCall` bypass Jinja Environment checks for `DeferredMarker`."""
    # DTFIX-MERGE: refine this API and make it public?
    _marker_attr: str = "_directcall"

    @classmethod
    def mark(cls, src: _TCallable) -> _TCallable:
        setattr(src, cls._marker_attr, True)
        return src

    @classmethod
    def is_marked(cls, value: t.Callable) -> bool:
        return callable(value) and getattr(value, "_directcall", False)


@_DirectCall.mark
def _query(plugin_name: str, /, *args, **kwargs) -> t.Any:
    """wrapper for lookup, force wantlist true"""
    kwargs['wantlist'] = True
    return _invoke_lookup(plugin_name=plugin_name, lookup_terms=list(args), lookup_kwargs=kwargs)


@_DirectCall.mark
def _lookup(plugin_name: str, /, *args, **kwargs) -> t.Any:
    # convert the args tuple to a list, since some plugins make a poor assumption that `run.args` is a list
    return _invoke_lookup(plugin_name=plugin_name, lookup_terms=list(args), lookup_kwargs=kwargs)


@_DirectCall.mark
def _invoke_lookup(*, plugin_name: str, lookup_terms: list, lookup_kwargs: dict[str, t.Any], te_invoking_action_name: str | None = None) -> t.Any:
    templar = TemplateContext.current().templar

    try:
        instance: LookupBase | None = lookup_loader.get(plugin_name, loader=templar._loader, templar=templar)
    except Exception as ex:
        raise AnsibleTemplatePluginLoadError('lookup', plugin_name) from ex

    if instance is None:
        raise AnsibleTemplatePluginNotFoundError('lookup', plugin_name)

    # if the lookup doesn't understand `DeferredMarker` and there's at least one in the top level, short-circuit by returning the first one we found
    if not instance.accept_deferred_marker and (first_marker := get_first_marker_arg(lookup_terms, lookup_kwargs)) is not None:
        return first_marker

    # don't pass these through to the lookup
    wantlist = lookup_kwargs.pop('wantlist', False)
    errors = lookup_kwargs.pop('errors', 'strict')

    with JinjaCallContext(
        eager_trip_deferred_marker=not instance.accept_deferred_marker,
        _te_invoking_action_name=te_invoking_action_name,
    ):
        # safely catch run failures per #5059
        try:
            if _TemplateConfig.allow_embedded_templates:
                # for backwards compat, only trust constant templates in lookup terms
                lookup_terms = templar.proxy_or_render_template(_trust_jinja_constants(lookup_terms))
            else:
                # not using proxy_args since it's a list, and we want to preserve tags
                lookup_terms = AnsibleTagHelper.tag_copy(lookup_terms, (proxy_jinja_constant_container(value) for value in lookup_terms), value_type=list)

            lookup_res = instance.run(lookup_terms, variables=templar.available_variables, **proxy_kwargs(lookup_kwargs))

            # DTFIX-FUTURE: Consider allowing/requiring lookup plugins to declare how their result should be handled.
            #        Currently there are multiple behaviors that are less than ideal and poorly documented (or not at all):
            #        * When `errors=warn` or `errors=ignore` the result is `None` unless `wantlist=True`, in which case the result is `[]`.
            #        * The user must specify `wantlist=True` to receive the plugin return value unmodified.
            #          A plugin can achieve similar results by wrapping its result in a list -- unless of course the user specifies `wantlist=True`.
            #        * When `wantlist=True` is specified, the result is not guaranteed to be a list as the option implies (except on plugin error).
            #        * Sequences are munged unless the user specifies `wantlist=True`:
            #          * len() == 0 - Return an empty sequence.
            #          * len() == 1 - Return the only element in the sequence.
            #          * len() >= 2 when all elements are `str` - Return all the values joined into a single comma separated string.
            #          * len() >= 2 when at least one element is not `str` - Return the sequence as-is.

            if not is_sequence(lookup_res):
                # DTFIX-FUTURE: deprecate return types which are not a list
                #   previously non-Sequence return types were deprecated and then became an error in 2.18
                #   however, the deprecation message (and this error) mention `list` specifically rather than `Sequence`
                #   letting non-list values through will trigger variable type checking warnings/errors
                raise TypeError(f'returned {type(lookup_res)} instead of {list}')

        except DeferredMarkerError as ex:
            return ex.source
        except Exception as ex:
            if isinstance(ex, AnsibleTemplatePluginError):
                msg = f'Lookup failed but the error is being ignored: {ex}'
            else:
                msg = f'An unhandled exception occurred while running the lookup plugin {plugin_name!r}. Error was a {type(ex)}, original message: {ex}'

            if errors == 'warn':
                _display.warning(msg)
            elif errors == 'ignore':
                _display.display(msg, log_only=True)
            else:
                raise AnsibleTemplatePluginRuntimeError('lookup', plugin_name) from ex

            return [] if wantlist else None

        if not wantlist and lookup_res:
            if len(lookup_res) == 1:
                lookup_res = lookup_res[0]
            else:
                try:
                    lookup_res = ",".join(lookup_res)  # for backwards compatibility, attempt to join `ran` into single string
                except TypeError:
                    pass  # for backwards compatibility, return `ran` as-is when the sequence contains non-string values

        return _wrap_plugin_output(lookup_res)


def _now(utc=False, fmt=None):
    """Jinja2 global function (now) to return current datetime, potentially formatted via strftime."""
    if utc:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    else:
        now = datetime.datetime.now()

    if fmt:
        return now.strftime(fmt)

    return now


def _trust_jinja_constants(o: t.Any) -> t.Any:
    """
    Recursively apply TrustedAsTemplate to values tagged with _JinjaConstTemplate and remove the tag.
    Only container types emitted by the Jinja compiler are checked, since others do not contain constants.
    This is used to provide backwards compatiblity with historical lookup behavior for positional arguments.
    """
    if _JinjaConstTemplate.is_tagged_on(o):
        return TrustedAsTemplate().tag(_JinjaConstTemplate.untag(o))

    o_type = type(o)

    if o_type is dict:
        return {k: _trust_jinja_constants(v) for k, v in o.items()}

    if o_type in (list, tuple):
        return o_type(_trust_jinja_constants(v) for v in o)

    return o


def _wrap_plugin_output(o: t.Any) -> t.Any:
    """Utility method to ensure that iterators/generators returned from a plugins are consumed."""
    if isinstance(o, _ITERATOR_TYPES):
        o = list(o)

    return o
