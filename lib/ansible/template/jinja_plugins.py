"""Jinja template plugins (filters, tests, lookups) and custom global functions."""

from __future__ import annotations

import collections.abc as c
import datetime
import functools
import traceback
import typing as t

from jinja2.exceptions import UndefinedError

from ..errors import AnsibleError, AnsibleTemplatePluginNotFoundError, AnsibleTemplatePluginError
from ..module_utils.common.collections import is_sequence
from ..plugins.loader import lookup_loader, Jinja2Loader
from ..utils.display import Display
from .utils import TemplateContext, _repr_from

_display = Display()


class JinjaPluginIntercept(c.MutableMapping):
    """
    Simulated dict class that loads Jinja2Plugins at request
    otherwise all plugins would need to be loaded a priori.

    NOTE: plugin_loader still loads all 'builtin/legacy' at
    start so only collection plugins are really at request.
    """

    def __init__(self, delegatee, pluginloader: Jinja2Loader, *args, **kwargs):

        super(JinjaPluginIntercept, self).__init__(*args, **kwargs)

        self._pluginloader = pluginloader

        # Jinja's environment mapping of known names (initially just J2 builtins)
        self._delegatee = delegatee

        # our names take precedence over Jinja's, but let things we've tried to resolve skip the pluginloader
        self._seen_it: set[str] = set()

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise ValueError('key must be a string, got %s instead' % type(key))

        original_exc = None
        if key not in self._seen_it:
            # This looks too early to set this, but it isn't. Setting it here keeps requests for Jinja builtins from
            # going through the pluginloader more than once, which is extremely slow for something that won't ever succeed.
            self._seen_it.add(key)
            plugin = None
            try:
                plugin = self._pluginloader.get(key)
            except (AnsibleError, KeyError) as e:
                original_exc = e
            except Exception as e:
                _display.vvvv(f'Unexpected plugin load ({key}) exception: {e}')
                raise e

            # if a plugin was found/loaded
            if plugin:
                # set in filter cache and avoid expensive plugin load
                self._delegatee[key] = plugin.j2_function

        # raise template syntax error if we could not find ours or jinja2 one
        try:
            func = self._delegatee[key]
        except KeyError:
            self._seen_it.remove(key)
            plugin_type = self._pluginloader.type
            message = f'{plugin_type} plugin {key!r} not found{": " + str(original_exc) if original_exc else ""}'
            raise AnsibleTemplatePluginNotFoundError(message) from original_exc

        # FIXME: can/should we handle this in finalize instead, or at least allow plugins to opt into/out of this behavior?
        # if i do have func and it is a filter, it needs wrapping
        if self._pluginloader.type == 'filter':
            # deprecated: description="deprecate STRING_TYPE_FILTERS config entry (formerly used here) once 2.18 is EOL" core_version="2.19"
            # conditionally unroll iterators/generators to avoid having to use `|list` after every filter
            func = self._wrap_filter(func, key)
        else:
            func = self._wrap_test(func, key)

        return func

    def __setitem__(self, key, value):
        return self._delegatee.__setitem__(key, value)

    def __delitem__(self, key):
        raise NotImplementedError()

    def __contains__(self, item: t.Any) -> bool:
        try:
            self.__getitem__(item)
        except AnsibleTemplatePluginNotFoundError:
            return False

        return True

    def __iter__(self):
        # not strictly accurate since we're not counting dynamically-loaded values
        return iter(self._delegatee)

    def __len__(self):
        # not strictly accurate since we're not counting dynamically-loaded values
        return len(self._delegatee)

    @staticmethod
    def _wrap_test(func: t.Callable, plugin_name: str) -> t.Callable:
        """Intercept point for all test plugins to ensure that args are properly templated/lazified."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> bool:
            # FIXME: see question in AnsibleEnvironment.call about needing to wrap input args
            tc = TemplateContext.current_or_raise()
            templar = tc.templar
            args = templar.proxy_or_render_template(args)
            kwargs = templar.proxy_or_render_kwargs(kwargs)

            try:
                test_res = func(*args, **kwargs)
            except UndefinedError:
                raise
            except Exception as ex:
                raise AnsibleTemplatePluginError(f"Test {plugin_name!r} failed: {ex}") from ex

            if not isinstance(test_res, bool):
                template = tc.template_value
                _display.deprecated(
                    msg=f"The test plugin {plugin_name!r} used in template {_repr_from(template)} returned a non-boolean result of type {type(test_res)!r}. "
                        f"Test plugins must have a boolean result.",
                    version="2.21",
                )
                test_res = bool(test_res)

            return test_res

        return wrapper

    @staticmethod
    def _wrap_filter(func: t.Callable, plugin_name: str) -> t.Callable:
        """Intercept point for all filter plugins to ensure that args are properly templated/lazified."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # FIXME: see question in AnsibleEnvironment.call about needing to wrap input args
            templar = TemplateContext.current_or_raise().templar
            args = templar.proxy_or_render_template(args)
            kwargs = templar.proxy_or_render_kwargs(kwargs)

            try:
                filter_res = func(*args, **kwargs)
            except UndefinedError:
                raise
            except Exception as ex:
                raise AnsibleTemplatePluginError(f"Filter {plugin_name!r} failed: {ex}") from ex

            return templar.proxy_or_render_template(filter_res)

        return wrapper


def _query(name, /, *args, **kwargs) -> t.Any:
    """wrapper for lookup, force wantlist true"""
    kwargs['wantlist'] = True
    return _lookup(name, *args, **kwargs)


def _lookup(name, /, *args, **kwargs) -> t.Any:
    templar = TemplateContext.current_or_raise().templar

    instance = lookup_loader.get(name, loader=templar._loader, templar=templar)

    if instance is None:
        raise AnsibleTemplatePluginNotFoundError(f"lookup plugin {name!r} not found")

    # some plugins make a poor assumption that `run` takes a list
    args = list(args)

    wantlist = kwargs.pop('wantlist', False)
    errors = kwargs.pop('errors', 'strict')

    # safely catch run failures per #5059
    try:
        ran = instance.run(args, variables=templar.available_variables, **kwargs)
    # FIXME: most of this exception handling should occur at the edge of templating
    except UndefinedError:
        # AnsibleUndefinedError - Don't wrap this, allowing template infrastructure to process it.
        raise
    # FIXME: collapse these two?
    except AnsibleTemplatePluginError as ex:
        # lookup handled error but still decided to bail
        msg = f'Lookup failed but the error is being ignored: {ex}'
        if errors == 'warn':
            _display.warning(msg)
        elif errors == 'ignore':
            _display.display(msg, log_only=True)
        else:
            raise AnsibleTemplatePluginError(f"Lookup {name!r} failed: {ex}") from ex
        return [] if wantlist else None
    except Exception as ex:
        # errors not handled by lookup
        msg = f'An unhandled exception occurred while running the lookup plugin {name!r}. Error was a {type(ex)}, original message: {ex}'
        if errors == 'warn':
            _display.warning(msg)
        elif errors == 'ignore':
            _display.display(msg, log_only=True)
        else:
            _display.vvv('exception during Jinja2 execution: {0}'.format(traceback.format_exc()))
            raise AnsibleTemplatePluginError(f"Lookup {name!r} failed: {ex}") from ex
        return [] if wantlist else None

    is_nonstring_sequence = is_sequence(ran)

    if not is_nonstring_sequence:
        _display.deprecated(
            f'The lookup plugin \'{name}\' was expected to return a list, got \'{type(ran)}\' instead. '
            f'The lookup plugin \'{name}\' needs to be changed to return a list. '
            'This will be an error in Ansible 2.18',
            version='2.18'
        )

    if ran:
        if wantlist:
            return ran

        try:
            if is_nonstring_sequence and len(ran) == 1:
                return ran[0]

            # FIXME: this seems wrong to do to a string output, but it's been that way forever?
            ran = ",".join(ran)
        except TypeError:
            # FIXME: is this reachable? If so, just return the list anyway...
            if not is_nonstring_sequence:
                raise AnsibleTemplatePluginError(f"Lookup {name!r} did not return a list.")
    return ran


def _now(utc=False, fmt=None):
    """Jinja2 global function (now) to return current datetime, potentially formatted via strftime."""
    if utc:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    else:
        now = datetime.datetime.now()

    if fmt:
        return now.strftime(fmt)

    return now
