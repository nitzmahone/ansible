# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import abc
import ast
import collections.abc as c
import dataclasses
import datetime
import functools
import os
import pwd
import secrets
import time
import types

import jinja2

import ansible.module_utils.compat.typing as t


from collections.abc import Iterator, Mapping, MappingView, MutableMapping
from contextlib import contextmanager
from itertools import islice
from traceback import format_exc

from jinja2.exceptions import TemplateSyntaxError, UndefinedError
from jinja2.loaders import FileSystemLoader
from jinja2.nativetypes import NativeCodeGenerator
from jinja2.runtime import Context, StrictUndefined, Undefined
from jinja2.nodes import Const
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.compiler import Frame
from jinja2.utils import missing

from ansible import constants as C
from ansible.errors import (
    AnsibleAssertionError,
    AnsibleConditionalError,
    AnsibleError,
    AnsibleFilterError,
    AnsibleLookupError,
    AnsibleValueOmittedError,
    AnsibleOptionsError,
    AnsibleUndefinedVariable,
)
from ansible.module_utils.six import string_types
from ansible.module_utils.common.text.converters import to_native, to_text, to_bytes
from ansible.module_utils.common.collections import is_sequence
from ansible.module_utils.datatag import AnsibleSourcePosition, AnsibleTaggedObject, TrustedAsTemplate, NotATemplate, NotTaggableError
from ansible.plugins.loader import filter_loader, lookup_loader, test_loader
from ansible.template.template import AnsibleJ2Template
from ansible.template.vars import AnsibleJ2Vars
from ansible.template.vault import _AnsibleTaggedVaultBomb, DetonateVaultBombsTripwire, UndecryptableAccessMutator
from ansible.module_utils.datatag import (
    Deprecated,
    _AnsibleTaggedDict,
    _AnsibleTaggedList,
    _AnsibleTaggedSet,
    _AnsibleTaggedTuple,
    _NO_INSTANCE_STORAGE,
    _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES,
    _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES,
    _try_get_internal_tags_mapping,
)
from ansible.module_utils.datatag.access import (
    AmbientContextBase,
    AnsibleAccessContext,
    POORLY_NAMED_SENTINEL,
    _NotifiableAccessContextBase,
)

from ansible.utils.display import Display
from ansible.utils.vars import isidentifier
from ansible.utils.datatag import AnsibleVariableTypeError

from collections import ChainMap


display = Display()


# FIXME: change this when overhauling the API
__all__ = ['Templar', 'generate_ansible_template_vars']

JINJA2_OVERRIDE = '#jinja2:'

JINJA2_BEGIN_TOKENS = frozenset(('variable_begin', 'block_begin', 'comment_begin', 'raw_begin'))
JINJA2_END_TOKENS = frozenset(('variable_end', 'block_end', 'comment_end', 'raw_end'))

RANGE_TYPE = type(range(0))

_ANSIBLE_LAZY_TEMPLATE_SLOTS = tuple(('_templar',))

# FIXME: remove/harden- just here for development backstop for now
if tuple(map(int, jinja2.__version__.split('.'))) < (3, 1):
    raise RuntimeError('Jinja 3.1+ required')


# FIXME: FDI028 - initial prototype, is this what we want?
#        should it be part of our public interface?
#        should this be part of AnsibleSourcePosition or otherwise in the datatag module_utils?
def _repr_from(value: t.Any) -> str:
    """Return the repr() of the given value, appending attribution of the source position, if available."""
    src_pos = AnsibleSourcePosition.get_tag(value)

    if src_pos:
        return f'{value!r} from {str(src_pos)!r}'

    return f'{value!r}'


class TemplateContext(AmbientContextBase):
    def __init__(self, *, template_value: t.Any, templar: Templar):
        self._template_value = template_value
        self._templar = templar

    @property
    def template_value(self) -> t.Any:
        return self._template_value

    @property
    def templar(self) -> Templar:
        return self._templar


def generate_ansible_template_vars(path, fullpath=None, dest_path=None):

    if fullpath is None:
        b_path = to_bytes(path)
    else:
        b_path = to_bytes(fullpath)

    try:
        template_uid = pwd.getpwuid(os.stat(b_path).st_uid).pw_name
    except (KeyError, TypeError):
        template_uid = os.stat(b_path).st_uid

    temp_vars = {
        'template_host': to_text(os.uname()[1]),
        'template_path': path,
        'template_mtime': datetime.datetime.fromtimestamp(os.path.getmtime(b_path)),
        'template_uid': to_text(template_uid),
        'template_run_date': datetime.datetime.now(),
        'template_destpath': to_native(dest_path) if dest_path else None,
    }

    if fullpath is None:
        temp_vars['template_fullpath'] = os.path.abspath(path)
    else:
        temp_vars['template_fullpath'] = fullpath

    managed_default = C.DEFAULT_MANAGED_STR
    managed_str = managed_default.format(
        host=temp_vars['template_host'],
        uid=temp_vars['template_uid'],
        file=temp_vars['template_path'].replace('%', '%%'),
    )
    temp_vars['ansible_managed'] = time.strftime(to_native(managed_str), time.localtime(os.path.getmtime(b_path)))

    return temp_vars


def _escape_backslashes(data: str, jinja_env: AnsibleEnvironment) -> str:
    """
    Escape backslashes in strings within Jinja template expressions to disable Jinja backslash processing.

    NOTE: This does *NOT* apply to strings within Jinja template statements ("{%" and "%}").

    This is useful when templates are sourced from YAML double-quoted strings, as it avoids having backslashes processed twice: first by the YAML parser,
    and then again by the Jinja parser. Instead, backslashes are only processed by YAML.

    Example YAML:

    - debug:
        msg: "Test Case 1\\3; {{ test1_name | regex_replace('^(.*)_name$', '\\1')}}"

    Since the outermost YAML string is double-quoted, the YAML parser converts the double backslashes to single backslashes. Without escaping, Jinja would see
    only a single backslash ('\1') while processing the embedded template expression, interpret it as an escape sequence, and convert it to '\x01'
    (ASCII "SOH"). This is clearly not the intended `\1` backreference argument to the `regex_replace` filter (which would require the double-escaped string
    '\\\\1' to yield the intended result).

    Since the "\\3" in the input YAML was not part of a template expression, the YAML-parsed "\3" remains after Jinja rendering. This would be
    confusing for playbook authors, as different escaping rules would be needed inside and outside the template expression.

    When templates are not sourced from YAML, escaping backslashes will prevent use of backslash escape sequences such as "\n" and "\t".

    See relevant Jinja lexer impl at e.g.: https://github.com/pallets/jinja/blob/3.1.2/src/jinja2/lexer.py#L646-L653.
    """
    if '\\' in data and jinja_env.variable_start_string in data:
        new_data = []
        d2 = jinja_env.preprocess(data)
        in_var = False

        for token in jinja_env.lex(d2):
            if token[1] == 'variable_begin':
                in_var = True
                new_data.append(token[2])
            elif token[1] == 'variable_end':
                in_var = False
                new_data.append(token[2])
            elif in_var and token[1] == 'string':
                # Double backslashes only if we're inside a jinja2 variable
                new_data.append(token[2].replace('\\', '\\\\'))
            else:
                new_data.append(token[2])

        data = ''.join(new_data)

    return data


def _create_overlay(data: str, overrides: dict, jinja_env: AnsibleEnvironment, undefined_behavior=None) -> tuple[str, AnsibleEnvironment, bool]:
    if overrides is None:
        overrides = {}

    try:
        has_override_header = data.startswith(JINJA2_OVERRIDE)
    except (TypeError, AttributeError):
        has_override_header = False

    if overrides or has_override_header or undefined_behavior:
        overlay = jinja_env.overlay(**overrides, undefined_behavior=undefined_behavior)
    else:
        overlay = jinja_env

    # Get jinja env overrides from template
    if has_override_header:
        eol = data.find('\n')
        line = data[len(JINJA2_OVERRIDE):eol]
        data = data[eol + 1:]
        for pair in line.split(','):
            if ':' not in pair:
                raise AnsibleError("failed to parse jinja2 override '%s'."
                                   " Did you use something different from colon as key-value separator?" % pair.strip())
            (key, val) = pair.split(':', 1)
            key = key.strip()
            if hasattr(overlay, key):
                setattr(overlay, key, ast.literal_eval(val.strip()))
            else:
                display.warning(f"Could not find Jinja2 environment setting to override: '{key}'")

    return data, overlay, has_override_header


def is_possibly_template(data, jinja_env):
    """Determines if a string looks like a template, by seeing if it
    contains a jinja2 start delimiter. Does not guarantee that the string
    is actually a template.

    This is different than ``is_template`` which is more strict.
    This method may return ``True`` on a string that is not templatable.

    Useful when guarding passing a string for templating, but when
    you want to allow the templating engine to make the final
    assessment which may result in ``TemplateSyntaxError``.
    """
    if isinstance(data, string_types):
        for marker in (jinja_env.block_start_string, jinja_env.variable_start_string, jinja_env.comment_start_string):
            if marker in data:
                return True
    return False


def is_template(data, jinja_env):
    """This function attempts to quickly detect whether a value is a jinja2
    template. To do so, we look for the first 2 matching jinja2 tokens for
    start and end delimiters.
    """
    found = None
    start = True
    comment = False
    d2 = jinja_env.preprocess(data)

    # Quick check to see if this is remotely like a template before doing
    # more expensive investigation.
    if not is_possibly_template(d2, jinja_env):
        return False

    # This wraps a lot of code, but this is due to lex returning a generator
    # so we may get an exception at any part of the loop
    try:
        for token in jinja_env.lex(d2):
            if token[1] in JINJA2_BEGIN_TOKENS:
                if start and token[1] == 'comment_begin':
                    # Comments can wrap other token types
                    comment = True
                start = False
                # Example: variable_end -> variable
                found = token[1].split('_')[0]
            elif token[1] in JINJA2_END_TOKENS:
                if token[1].split('_')[0] == found:
                    return True
                elif comment:
                    continue
                return False
    except TemplateSyntaxError:
        return False

    return False


def _count_newlines_from_end(in_str):
    '''
    Counts the number of newlines at the end of a string. This is used during
    the jinja2 templating to ensure the count matches the input, since some newlines
    may be thrown away during the templating.
    '''

    try:
        i = len(in_str)
        j = i - 1
        while in_str[j] == '\n':
            j -= 1
        return i - 1 - j
    except IndexError:
        # Uncommon cases: zero length string and string containing only newlines
        return i


def recursive_check_defined(item):
    from jinja2.runtime import Undefined

    if isinstance(item, MutableMapping):
        for key in item:
            recursive_check_defined(item[key])
    elif isinstance(item, list):
        for i in item:
            recursive_check_defined(i)
    else:
        if isinstance(item, Undefined):
            raise AnsibleFilterError("{0} is undefined".format(item))


def _is_rolled(value):
    """Helper method to determine if something is an unrolled generator,
    iterator, or similar object
    """
    return (
        isinstance(value, Iterator) or
        isinstance(value, MappingView) or
        isinstance(value, RANGE_TYPE)
    )


def _unroll_iterator(func):
    """Wrapper function, that intercepts the result of a templating
    and auto unrolls a generator, so that users are not required to
    explicitly use ``|list`` to unroll.
    """
    def wrapper(*args, **kwargs):
        ret = func(*args, **kwargs)
        if _is_rolled(ret):
            return list(ret)
        return ret

    return functools.update_wrapper(wrapper, func)


class AnsibleUndefined(StrictUndefined):
    '''
    A custom Undefined class, which returns further Undefined objects on access,
    rather than throwing an exception.
    '''
    __slots__ = ('_undefined_template_source',)

    def __init__(
            self,
            hint: t.Optional[str] = None,
            obj: t.Any = missing,
            name: t.Optional[str] = None,
            *args,
            template_source: str | None = None,
            **kwargs,
    ):
        if not hint and name and obj is not missing:
            obj_type_name = (obj.native_type if isinstance(obj, AnsibleTaggedObject) else type(obj)).__name__
            hint = f"object of type {obj_type_name!r} has no attribute {name!r}"

        kwargs.update(hint=hint, obj=obj, name=name)
        super().__init__(*args, **kwargs)
        self._undefined_template_source = template_source

    def __getattr__(self, name):
        # Return original Undefined object to preserve the first failure context
        return self

    def __getitem__(self, key):
        # Return original Undefined object to preserve the first failure context
        return self

    def __repr__(self):
        return 'AnsibleUndefined(hint={0!r}, obj={1!r}, name={2!r})'.format(
            self._undefined_hint,
            self._undefined_obj,
            self._undefined_name
        )

    def __contains__(self, item):
        # Return original Undefined object to preserve the first failure context
        return self


class AnsibleContext(Context):
    """
    A custom context which intercepts resolve_or_missing() calls and
    runs them through AnsibleAccessContext. This allows usage of variables
    to be tracked. If needed, values can also be modified before being returned.
    """
    def __init__(self, *args, **kwargs):
        super(AnsibleContext, self).__init__(*args, **kwargs)

    def resolve_or_missing(self, key):
        val = super(AnsibleContext, self).resolve_or_missing(key)
        return AnsibleAccessContext.current().access(val)

    def get_all(self):
        """Return the complete context as a dict including the exported
        variables. For optimizations reasons this might not return an
        actual copy so be careful with using it.

        This is to prevent from running ``AnsibleJ2Vars`` through dict():

            ``dict(self.parent, **self.vars)``

        In Ansible this means that ALL variables would be templated in the
        process of re-creating the parent because ``AnsibleJ2Vars`` templates
        each variable in its ``__getitem__`` method. Instead we re-create the
        parent via ``AnsibleJ2Vars.add_locals`` that creates a new
        ``AnsibleJ2Vars`` copy without templating each variable.

        This will prevent unnecessarily templating unused variables in cases
        like setting a local variable and passing it to {% include %}
        in a template.

        Also see ``AnsibleJ2Template``and
        https://github.com/pallets/jinja/commit/d67f0fd4cc2a4af08f51f4466150d49da7798729
        """
        if not self.vars:
            return self.parent
        if not self.parent:
            return self.vars

        if isinstance(self.parent, AnsibleJ2Vars):
            return self.parent.add_locals(self.vars)
        else:
            # can this happen in Ansible?
            return dict(self.parent, **self.vars)


class DeprecatedAccessAuditContext(_NotifiableAccessContextBase):
    _tag_type_interest = frozenset([Deprecated])

    def __init__(self) -> None:
        self._tripped_deprecation_info: t.List[t.Tuple[t.Any, Deprecated]] = []

    def _notify(self, o: t.Any) -> t.Any:
        deprecated = Deprecated.get_tag(o)

        if deprecated:
            current_template = TemplateContext.current()
            template = current_template.template_value if current_template else None
            self._tripped_deprecation_info.append((template, deprecated))

        return POORLY_NAMED_SENTINEL

    @property
    def deprecated_access(self) -> t.Tuple[t.Tuple[t.Any, Deprecated], ...]:
        return tuple(self._tripped_deprecation_info)


class JinjaPluginIntercept(MutableMapping):
    ''' Simulated dict class that loads Jinja2Plugins at request
        otherwise all plugins would need to be loaded a priori.

        NOTE: plugin_loader still loads all 'builtin/legacy' at
        start so only collection plugins are really at request.
    '''

    def __init__(self, delegatee, pluginloader, *args, **kwargs):

        super(JinjaPluginIntercept, self).__init__(*args, **kwargs)

        self._pluginloader = pluginloader

        # Jinja environment's mapping of known names (initially just J2 builtins)
        self._delegatee = delegatee

        # our names take precedence over Jinja's, but let things we've tried to resolve skip the pluginloader
        self._seen_it = set()

    def __getitem__(self, key):
        if not isinstance(key, string_types):
            raise ValueError('key must be a string, got %s instead' % type(key))

        original_exc = None
        if key not in self._seen_it:
            # this looks too early to set this- it isn't. Setting it here keeps requests for Jinja builtins from
            # going through the pluginloader more than once, which is extremely slow for something that won't ever succeed.
            self._seen_it.add(key)
            plugin = None
            try:
                plugin = self._pluginloader.get(key)
            except (AnsibleError, KeyError) as e:
                original_exc = e
            except Exception as e:
                display.vvvv('Unexpected plugin load (%s) exception: %s' % (key, to_native(e)))
                raise e

            # if a plugin was found/loaded
            if plugin:
                # set in filter cache and avoid expensive plugin load
                self._delegatee[key] = plugin.j2_function

        # raise template syntax error if we could not find ours or jinja2 one
        try:
            func = self._delegatee[key]
        except KeyError as e:
            self._seen_it.remove(key)
            raise TemplateSyntaxError('Could not load "%s": %s' % (key, to_native(original_exc or e)), 0)

        # FIXME: can/should we handle this in finalize instead, or at least allow plugins to opt into/out of this behavior?
        # if i do have func and it is a filter, it needs wrapping
        if self._pluginloader.type == 'filter':
            # deprecated: description="deprecate STRING_TYPE_FILTERS config entry (formerly used here) once 2.18 is EOL" core_version="2.19"
            # conditionally unroll iterators/generators to avoid having to use `|list` after every filter
            func = _unroll_iterator(func)

        return func

    def __setitem__(self, key, value):
        return self._delegatee.__setitem__(key, value)

    def __delitem__(self, key):
        raise NotImplementedError()

    def __iter__(self):
        # not strictly accurate since we're not counting dynamically-loaded values
        return iter(self._delegatee)

    def __len__(self):
        # not strictly accurate since we're not counting dynamically-loaded values
        return len(self._delegatee)


# NB: we're not actually using this pass_context, but it prevents our finalizer from
#  being called on constants at template compile time, which also allows our custom
#  visit_Const override to be used to mark embedded template constants trusted.
@jinja2.pass_context
def _ansible_finalize(ctx, thing):
    """
    This function is called by Jinja with the result of each
    variable template block (eg {{ }}) encountered in a template. It
    converts iterator results into lists, (recursively) ensures that no Undefined
    values exist in the result, and coalesces None to empty string (for backward
    compatibility).
    """

    if _is_rolled(thing):
        thing = list(thing)

    # FIXME: do this on the output of do_template?
    return thing if thing is not None else ''


# FIXME: find this a better home?
class _OmitType:
    """
    A placeholder singleton used to dynamically omit items from a dict/list/tuple/set when the value is `Omit`.

    The Omit singleton value is accessible from all Ansible templating contexts via the Jinja global
    name `omit`. Item removal occurs during final recursive processing of template results. The singleton
    `Omit` placeholder value will be visible to plugins during templating. The only time a template result
    will include `Omit` outside a templating context is when the template renders to the scalar value `Omit`.
    """
    __slots__ = ()

    # FIXME: this keeps pickle happy, but not JSON/YAML for callbacks; just teach them about it?
    def __new__(cls):
        return Omit

    def __repr__(self):
        return "<<Omit>>"


Omit = object.__new__(_OmitType)


class _AnsibleLazyTemplateMixin:
    __slots__ = _NO_INSTANCE_STORAGE

    # static dispatch entries for scalar types are listed here
    # additional dispatch entries for container types are populated by our __init_subclass__
    _dispatch_types: dict[type, type[AnsibleTaggedObject] | None] = {scalar_type: None for scalar_type in _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES}

    # due to the way Jinja handles globals, we may encounter things like functions/methods in hooked getitem/getattr that
    # always pass through this mixin; we want to silently ignore those types
    # FIXME: optimize this list by separating base types (using isinstance) from exact types using a set lookup
    _ignore_types = (
        types.MethodType,
        # FIXME: is there a better way to include callables like these, so we're not playing whack-a-mole
        type(''.startswith),  # builtin_function_or_method
        type(Omit),
        Undefined,
        StrictUndefined,
        AnsibleUndefined,
    )

    _container_types: set[type] = set()  # populated by our __init_subclass__

    def __init_subclass__(cls, **kwargs) -> None:
        # FIXME: this determination is very fragile to new layers added to the hierarchy
        tagged_type = cls.__mro__[1]
        native_type = tagged_type.__mro__[1]

        cls._dispatch_types[native_type] = t.cast(type[AnsibleTaggedObject], cls)
        cls._dispatch_types[tagged_type] = t.cast(type[AnsibleTaggedObject], cls)
        cls._dispatch_types[cls] = None

        cls._container_types.add(native_type)
        cls._empty_tags_as_native = False  # never revert to the native type when no tags remain

    def __init__(self):
        if not (tc := TemplateContext.current()):
            # FIXME: better exception type?
            raise ReferenceError("no TemplateContext is available")

        self._templar = tc.templar  # pylint: disable=assigning-non-slot  # slot defined in derived type

    @staticmethod
    def try_create(item: t.Any) -> t.Any:
        # FIXME: should we be supporting arbitrary sequences and mappings here?

        # FIXME: this double-copy is very wasteful- optimize with a new "wrap_with_type" classmethod on
        #  AnsibleTaggedObject or ? Also, maybe augment AnsibleTaggedObject._tag_value with the ability to force the wrapper
        #  type or an alternate type map instead?

        # FIXME: add an optimization to avoid looking at tagged types for entire categories of things we're not interested in
        # FIXME: consider optimizing empty container case (return input)?

        item_type = type(item)

        # try to use exact type match first to determine which wrapper (if any) to apply; isinstance checks
        # are extremely expensive, so try to avoid them for our commonly-supported types
        if not (dispatcher := _AnsibleLazyTemplateMixin._dispatch_types.get(item_type, ...)):
            return item

        # from this point on, we're always going to create a taggable type
        if dispatcher is ...:
            # we've deferred the expensive isinstance checks as late as possible
            for container_type in _AnsibleLazyTemplateMixin._container_types:
                if isinstance(item, container_type):
                    display.warning(f'Converting unsupported {item_type} to {container_type}.')
                    dispatcher = _AnsibleLazyTemplateMixin._dispatch_types[container_type]
                    break
            else:
                # FIXME: what do we want here? such as HostVars, HostVarsVars
                # FIXME: we now have strict checking of variable types leaving templating, is this warning redundant?
                # FIXME: undefined types need to be here too? (prevent warnings from with_first_found loops with undefined values)
                if not isinstance(item, _AnsibleLazyTemplateMixin._ignore_types):
                    display.warning(f'Encountered unsupported {item_type} type.')

                return item

        tags_mapping = _try_get_internal_tags_mapping(item)
        value = dispatcher._instance_factory(item, tags_mapping)

        return value


@t.final
class _AnsibleLazyTemplateDict(_AnsibleTaggedDict, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item: t.Any) -> t.Any:
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    # FIXME: fully implement iteration support
    # FIXME: do we need to implement templated key support?

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return dict.__repr__(dict(self.items()))

    def items(self):
        for key, value in super().items():
            # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
            #  template the same values?
            yield key, self._templar.environment._proxy_or_render_template(value, key)

    def native_copy(self) -> dict:
        return dict(dict.items(self))


@t.final
class _AnsibleLazyTemplateList(_AnsibleTaggedList, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item):
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return list.__repr__(list(self.__iter__()))

    def native_copy(self) -> list:
        return list(list.__iter__(self))


@t.final
class _AnsibleLazyTemplateTuple(_AnsibleTaggedTuple, _AnsibleLazyTemplateMixin):
    # nonempty __slots__ not supported for subtype of 'tuple'

    def __init__(self, *args, **kwargs):
        # NB: we're explicitly not calling super().__init__ here, since our hierarchy doesn't need it, and tuple's init is
        # object.__init__, which accepts no args beyond "self"
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item):
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return tuple.__repr__(tuple(self.__iter__()))

    def native_copy(self) -> tuple:
        return tuple(tuple.__iter__(self))


@t.final
class _AnsibleLazyTemplateSet(_AnsibleTaggedSet, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return set.__repr__(set(self.__iter__()))

    def native_copy(self) -> set:
        return set(set.__iter__(self))


class AnsibleNativeCodeGenerator(NativeCodeGenerator):
    # prevent Jinja's code generation from stringifying single nodes before generating its repr
    # (this complements the behavioral change in our concat)
    # FIXME: contribute this back upstream as a fix to Jinja's native support?
    def _output_const_repr(self, group: t.Iterable[t.Any]) -> str:
        group_list = list(group)

        if len(group_list) == 1:
            return repr(group_list[0])
        return repr("".join(map(str, group_list)))

    # this override causes embedded inline template strings to be marked TrustedAsTemplate at runtime
    # so that some inline templates can be processed with multiple passes, eg, {{ lookup("file", "{{output_dir}}/bla") }}
    def visit_Const(self, node: Const, frame: Frame) -> None:
        # FIXME: shortcut "is maybe template", then blindly wrap with TrustedAsTemplate if so
        # FIXME: this needs to consult the variable marker overrides
        is_template = type(node.value) is str and '{{' in node.value  # pylint: disable=unidiomatic-typecheck

        val = node.as_const(frame.eval_ctx)
        if isinstance(val, float):
            self.write(str(val))
        elif is_template:
            # FIXME: propagate other tags from parent template (for forensic/debug)?
            # FIXME: if lookup nerfing is restored, this could end up assigning trust to an embedded constant we don't want to trust.
            #  Keep this note until we're sure it's not coming back.
            self.write(f'environment._render_inline_template({val!r})')
        else:
            self.write(repr(val))


# FIXME: bikeshed name/placement/access/singleton
# FIXME: these look an awful lot like Jinja finalizers (which are also owned by the environment); as part of Jinja-normalization, should we wrap this and all
#  other custom post-templating behavior into a Jinja finalizer?
class UndefinedBehavior(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def handle_undefined(self, value: Undefined) -> t.Any:
        ...

    def post_finalize(self, template_result: t.Any) -> t.Any:
        return template_result


# FUTURE: do we want an option to let these accumulate so we can report > 1 failure at a time?
class FailOnUndefined(UndefinedBehavior):
    def handle_undefined(self, value):
        if isinstance(value, Undefined):
            hint = value._undefined_hint
        else:
            hint = None

        if not hint:
            hint = '<no hint>'

        raise AnsibleUndefinedVariable(f"undefined BLAH FIXME: {hint}", obj=value)


FAIL_ON_UNDEFINED: t.Final = FailOnUndefined()  # no sense in making many instances...


class BestEffort(UndefinedBehavior):
    def __init__(self) -> None:
        self._undefined_templates: list[Undefined] = []

    @staticmethod
    def _hint(value: Undefined):
        try:
            # FIXME: need to come up with a better way to capture the context when Jinja gives us not much to go on, or maybe inject our own
            hint = value._undefined_template_source or value._undefined_hint

            if not hint and (hint := value._undefined_name):
                hint = f'{{{{ {hint} }}}}'
        except AttributeError:
            hint = None

        if not hint:
            hint = "FIXME shrug"

        return hint

    def handle_undefined(self, value: Undefined) -> t.Any:
        self._undefined_templates.append(value)
        # FIXME: figure out how/where to propagate this as a failure to the TemplateResult

        return NotATemplate().tag(self._hint(value))

    @property
    def has_warnings(self) -> bool:
        return bool(self._undefined_templates)

    def warnings(self, max_count: int | None = None) -> c.Generator[str, None, None]:
        try:
            # blah = list(f'FIXME busted template {self._hint(w)}' for w in islice(self._undefined_templates, max_count))
            # yield from blah
            for w in islice(self._undefined_templates, max_count):
                try:
                    yield NotATemplate().tag(f'FIXME busted template {self._hint(w)}')
                except Exception as exi:
                    raise
        except Exception as e:
            raise


class BestEffortOmitUndefined(BestEffort):
    def handle_undefined(self, value: Undefined) -> t.Any:
        self._undefined_templates.append(value)

        return Omit

    def post_finalize(self, template_result: t.Any) -> t.Any:
        if not self.has_warnings:
            return template_result

        # there were warnings, which means we emitted omits that need omitting into the template result
        # do another finalize pass to clean it up
        return _finalize_template_result(template_result, undefined_behavior=FAIL_ON_UNDEFINED, raise_on_unsupported_type=False)


class AnsibleEnvironment(ImmutableSandboxedEnvironment):
    '''
    Our custom environment, which simply allows us to override the class-level
    values for the Template and Context classes used by jinja2 internally.
    '''
    context_class = AnsibleContext
    template_class = AnsibleJ2Template
    code_generator_class = AnsibleNativeCodeGenerator

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.filters = JinjaPluginIntercept(self.filters, filter_loader)
        self.tests = JinjaPluginIntercept(self.tests, test_loader)

        # future Jinja releases may default-enable autoescape; force-disable to prevent the problems it could cause
        # see https://github.com/pallets/jinja/blob/3.1.2/docs/api.rst?plain=1#L69
        self.autoescape = False

        self.trim_blocks = True

        self.undefined = AnsibleUndefined
        self.finalize = _ansible_finalize
        self.undefined_behavior = FAIL_ON_UNDEFINED

        self.globals.update(
            range=range,  # the sandboxed environment limits range in ways that may cause us problems; use the real Python one
            now=self._now,
            undef=self._undef,
            omit=Omit,
        )

        # Disabling the optimizer prevents compile-time constant expression folding, which prevents our
        # visit_Const recursive inline template expansion tricks from working in many cases where Jinja's
        # ignorance of our embedded templates are optimized away as fully-constant expressions,
        # eg {{ "{{'hi'}}" == "hi" }}. As of Jinja ~3.1, this specifically avoids cases where the @optimizeconst
        # visitor decorator performs constant folding, which bypasses our visit_Const impl and causes embedded
        # templates to be lost.
        # See also optimizeconst impl: https://github.com/pallets/jinja/blob/3.1.0/src/jinja2/compiler.py#L48-L49
        self.optimized = False

        self.template_class.environment_class = AnsibleEnvironment  # FIXME: why is this here? -- it was moved from Templar.__init__ (environment creation)

    def overlay(self, *args, undefined_behavior: t.Callable[..., t.Any] = None, **kwargs):
        res = super().overlay(*args, **kwargs)
        res.undefined_behavior = undefined_behavior or self.undefined_behavior
        return res

    def concat(self, nodes: t.Iterable[t.Any]) -> t.Any:  # type: ignore[override]
        node_list = list(nodes)
        if not node_list:
            return ''

        # this code is complemented by our tweaked CodeGenerator _output_const_repr that ensures that literal constants
        # in templates aren't double-repr'd in the generated code
        if len(node_list) == 1:
            # FIXME: do we WANT to allow nulls? FDI025
            if node_list[0] is None:
                return ''
            return AnsibleAccessContext.current().access(node_list[0])

        # FIXME: need to smuggle undefined_behavior in from the current templating operation (eg, debug and templated task names w/ BestEffort)
        # in order to ensure that all embedded triggers fire (vaultbomb, undefined, etc), do a recursive finalize before we repr (otherwise we can end up
        # repr'ing Undefineds etc). Yes, this requires two passes, but means we don't need to have a parallel reimplementation of all reprs
        node_list = _finalize_template_result(node_list, undefined_behavior=self.undefined_behavior, raise_on_unsupported_type=False)

        # FIXME: determine if we should do managed access here (we *should* have hit them all during templating/resolve, but ?)
        return ''.join([to_text(v) for v in node_list])

    # NB: this method is for exclusive use of the template compiler to render embedded constant templates
    def _render_inline_template(self, const_template: str) -> t.Any:
        const_template = TrustedAsTemplate().tag(const_template)
        result = self._proxy_or_render_template(const_template)
        return result

    def getitem(self, obj, argument):
        # FIXME: do we actually need to managed-access both sides of templates/strings here?
        return self._proxy_or_render_template(super().getitem(obj, argument), argument)

    def getattr(self, obj, attribute):
        return self._proxy_or_render_template(super().getattr(obj, attribute), attribute)

    def _proxy_or_render_template(self, item: t.Any, key: str | None = None):
        # FIXME: always blindly access item here?
        item = AnsibleAccessContext.current().access(item)
        if isinstance(item, str):
            # in case the item is a template, render it first
            if not (template_context := TemplateContext.current()):
                # FIXME: better exception type? (same thing in the lazy template wrapper constructors)
                raise ReferenceError("no TemplateContext is available")
            try:
                # FIXME: we need to propagate template args like undefined_behavior and/or move them into a templar/overlay instance
                #  also, what happens if Lazy's that survive encounter a different templar and/or override args
                item = template_context.templar.template(item)
            except (AnsibleUndefinedVariable, UndefinedError) as e:  # FIXME: can we dump this whole thing or preserve just enough?
                # Instead of failing here prematurely, return an Undefined
                # object which fails only after its first usage allowing us to
                # do lazy evaluation and passing it into filters/tests that
                # operate on such objects.
                return AnsibleUndefined(
                    template_source=item,
                    hint=e.message,  # FIXME: what should this actually be?
                    name=key,
                    exc=AnsibleUndefinedVariable,
                )
            except Exception as e:
                msg = getattr(e, 'message', None) or to_native(e)
                raise AnsibleError(
                    f"An unhandled exception occurred while templating '{to_native(item)}'. "
                    f"Error was a {type(e)}, original message: {msg}"
                )

        # FIXME: this can return an empty lazy container, is that what we want?
        if (lazy := _AnsibleLazyTemplateMixin.try_create(item)) is not None:
            return lazy

        return item

    def _now(self, utc=False, fmt=None):
        """Jinja2 global function (now) to return current datetime, potentially formatted via strftime."""
        if utc:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        else:
            now = datetime.datetime.now()

        if fmt:
            return now.strftime(fmt)

        return now

    def _undef(self, hint=None):
        """Jinja2 global function (undef) for creating custom undefined defaults with custom hints."""
        if hint is None or isinstance(hint, Undefined) or hint == '':
            hint = "Mandatory variable has not been overridden"

        return AnsibleUndefined(hint)


# FIXME: do we still need a class for this?
@dataclasses.dataclass(frozen=True, kw_only=True, slots=True)
class TemplateResult:
    result: t.Any

    def as_text(self):
        result = self.result
        return AnsibleTaggedObject.tag(str(result), AnsibleTaggedObject.tags(result) | {NotATemplate()})


class Templar:
    '''
    The main class for templating, with the main entry-point of template().
    '''

    def __init__(self, loader, variables=None):
        self._loader = loader
        self._available_variables = {} if variables is None else variables

        self._fail_on_undefined_errors = C.DEFAULT_UNDEFINED_VAR_BEHAVIOR

        self.environment = AnsibleEnvironment(
            extensions=self._get_extensions(),
            loader=FileSystemLoader(loader.get_basedir() if loader else '.'),
        )

        # FIXME: move all this magic under our Jinja environment?

        # Custom globals
        self.environment.globals['lookup'] = self._lookup
        self.environment.globals['query'] = self.environment.globals['q'] = self._query_lookup

    # FIXME: this needs to die, badly
    def copy_with_new_env(self, **kwargs):
        r"""Creates a new copy of Templar with a new environment.

        :kwarg \*\*kwargs: Optional arguments for the new environment that override existing
            environment attributes.

        :returns: Copy of Templar with updated environment.
        """
        # We need to use __new__ to skip __init__, mainly not to create a new
        # environment there only to override it below
        new_env = object.__new__(AnsibleEnvironment)
        new_env.__dict__.update(self.environment.__dict__)

        new_templar = object.__new__(Templar)
        new_templar.__dict__.update(self.__dict__)
        new_templar.environment = new_env

        mapping = {
            'available_variables': new_templar,
            'searchpath': new_env.loader,
        }

        for key, value in kwargs.items():
            obj = mapping.get(key, new_env)
            try:
                if value is not None:
                    setattr(obj, key, value)
            except AttributeError:
                # Ignore invalid attrs
                pass

        return new_templar

    def _get_extensions(self):
        '''
        Return jinja2 extensions to load.

        If some extensions are set via jinja_extensions in ansible.cfg, we try
        to load them with the jinja environment.
        '''

        jinja_exts = []
        if C.DEFAULT_JINJA2_EXTENSIONS:
            # make sure the configuration directive doesn't contain spaces
            # and split extensions in an array
            jinja_exts = C.DEFAULT_JINJA2_EXTENSIONS.replace(" ", "").split(',')

        return jinja_exts

    @property
    def available_variables(self):
        return self._available_variables

    @available_variables.setter
    def available_variables(self, variables):
        '''
        Sets the list of template variables this Templar instance will use
        to template things, so we don't have to pass them around between
        internal methods. We also clear the template cache here, as the variables
        are being changed.
        '''

        if not isinstance(variables, Mapping):
            raise AnsibleAssertionError("the type of 'variables' should be a Mapping but was a %s" % (type(variables)))
        self._available_variables = variables

    @contextmanager
    def set_temporary_context(self, **kwargs):
        """Context manager used to set temporary templating context, without having to worry about resetting
        original values afterward

        Use a keyword that maps to the attr you are setting. Applies to ``self.environment`` by default, to
        set context on another object, it must be in ``mapping``.
        """
        mapping = {
            'available_variables': self,
            'searchpath': self.environment.loader,
        }
        original = {}

        for key, value in kwargs.items():
            obj = mapping.get(key, self.environment)
            try:
                original[key] = getattr(obj, key)
                if value is not None:
                    setattr(obj, key, value)
            except AttributeError:
                # Ignore invalid attrs
                pass

        yield

        for key in original:
            obj = mapping.get(key, self.environment)
            setattr(obj, key, original[key])

    # FIXME: ditch this?
    def resolve_variable_expression(self, expression: str) -> t.Any:
        """Resolve a variable name or simple dotted variable expression."""
        stripped_expression = expression.strip()
        components = stripped_expression.split('.')
        if not all(map(isidentifier, components)):
            raise AnsibleError(f'invalid variable expression: {expression}')
        return self.__template_expression(stripped_expression)

    # FIXME: implement a pylint check for proper usage of LiteralString args (even if mypy eventually supports it,
    # we'll want this to get checked for collections, too).
    def template_literal_expression(self, expression: t.LiteralString, var_overrides: dict[str, t.Any] | None = None) -> t.Any:
        """Template string literal expressions with blind trust."""
        return self.__template_expression(expression, var_overrides=var_overrides)

    def variable_name_as_template(self, name: str) -> str:
        stripped_name = name.strip()
        if not isidentifier(stripped_name):
            # FIXME: better exception type here
            raise AnsibleError(f"invalid variable name: {stripped_name}")
        # FIXME: propagate other tags? (source position, etc)
        return TrustedAsTemplate().tag('{{' + stripped_name + '}}')

    def __template_expression(self, expression: str, var_overrides: dict[str, t.Any] | None = None) -> t.Any:
        """Template string expressions with blind trust."""
        # FIXME: propagate other tags? (source position, etc)
        expression_template = TrustedAsTemplate().tag('{{' + expression + '}}')
        variables = ChainMap(var_overrides, self._available_variables) if var_overrides else self._available_variables
        templar = Templar(self._loader, variables=variables)
        return templar.template(expression_template)

    # FIXME: wrap tripwires in a template decorator so we can preserve/propagate args automatically
    # FIXME: static_vars is dead (long live NotATemplate); kill it from intermediate signatures and (possibly?) deprecation warning
    def template(self, *args, **kwargs) -> t.Any:
        return self.template_with_result(*args, **kwargs).result

    def template_with_result(self, variable, *, preserve_trailing_newlines=True, escape_backslashes=True, fail_on_undefined=None,
                             overrides=None, convert_data=True, static_vars=None, cache=None, disable_lookups=False, undefined_behavior=FAIL_ON_UNDEFINED,
                             stop_on_container_result=False, value_for_omit=Omit) -> TemplateResult:
        """Templates (possibly recursively) any given data as input."""

        # bail out if we know we're looking at something that's been explicitly tagged as not a template
        if variable is None or NotATemplate.is_tagged_on(variable):
            return TemplateResult(result=variable)  # input was not manipulated, trust that it contains only allowed types

        # FIXME: nuke
        if fail_on_undefined is None:
            fail_on_undefined = self._fail_on_undefined_errors

        template_kwargs = dict(
            preserve_trailing_newlines=preserve_trailing_newlines,
            escape_backslashes=escape_backslashes,
            fail_on_undefined=fail_on_undefined,
            overrides=overrides,
            cache=cache,
            disable_lookups=disable_lookups,
            undefined_behavior=undefined_behavior,
        )

        # FIXME: early exit on empty collections

        # track access to items that are tagged Deprecated during templating, handle accordingly
        with (
                UndecryptableAccessMutator(),  # trigger injection of VaultBomb
                DeprecatedAccessAuditContext() as deprecated,
        ):
            _template_result = self._template_recursive(variable, **template_kwargs)

            # If we're the outermost template operation, and our input was a string template whose result was NOT a string,
            # we need to do one last recursive template pass over the resulting container (since we're not doing it on Jinja resolve anymore). This will
            # ensure that we never allow containers with untemplated strings to escape the template system, and that any
            # embedded Undefined values we encounter will raise AnsibleUndefinedError if fail_on_undefined is set (FIXME once we actually do that).
            if not TemplateContext.current():
                if _template_result is Omit:
                    if value_for_omit is Omit:
                        raise AnsibleValueOmittedError()

                    return TemplateResult(result=value_for_omit)  # value_for_omit was not manipulated, trust that it contains only allowed types

                if stop_on_container_result and type(_template_result) in _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES:
                    # Use of stop_on_container_result implies the caller will perform necessary checks on values,
                    # most likely by passing them back into the templating system.
                    return TemplateResult(
                        result=_template_result.native_copy() if _template_result in AnsibleTaggedObject._collection_types else _template_result,
                    )

                # data is our only positional arg, everything else is kwargs-only
                with DetonateVaultBombsTripwire(), TemplateContext(template_value=_template_result, templar=self):
                    _template_result = _finalize_template_result(_template_result, undefined_behavior=undefined_behavior, raise_on_unsupported_type=True)
                    _template_result = undefined_behavior.post_finalize(_template_result)

                # FIXME: this may not be needed now that we always finalize, trying it commented out
                # if undecryptable.is_tripped:
                #     # we encountered at least one UndecryptableVaultedValue; raise an error if any remain in the result
                #     self._detonate_vault_bombs(_template_result)

        # FIXME: create a dataclass or something for runtime capture of deprecation info plus the template context the access occurred in
        for deprecation_template, deprecation in deprecated.deprecated_access:
            # FIXME: if we're in a worker, propagate deprecated access warnings back to the controller for deduplication
            # FIXME: the current template may not have a source position, we may need to consult a parent template
            message = deprecation.msg

            if deprecation_template is not None:
                message += f' while templating {_repr_from(deprecation_template)}'

            display.deprecated(message, version=deprecation.removal_version, date=deprecation.removal_date)

        return TemplateResult(result=_template_result)

    def _template_recursive(self, variable, *, undefined_behavior, preserve_trailing_newlines=True, escape_backslashes=True,
                            fail_on_undefined=None, overrides=None, cache=None, disable_lookups=False):
        """Templates (possibly recursively) any given data as input."""
        # stack the current active var value we're templating; this lets the deprecated tripwire ask for it
        with TemplateContext(template_value=variable, templar=self):
            # FIXME: ensure tag propagation behavior is working for containers

            if cache is not None:
                display.deprecated("The `cache` option to `Templar.template` is no longer functional, and will be removed in a future release.", version='2.18')

            if isinstance(variable, string_types):
                if not self.is_possibly_template(variable, overrides):
                    return variable

                result = self.do_template(
                    variable,
                    preserve_trailing_newlines=preserve_trailing_newlines,
                    escape_backslashes=escape_backslashes,
                    fail_on_undefined=fail_on_undefined,
                    overrides=overrides,
                    disable_lookups=disable_lookups,
                    undefined_behavior=undefined_behavior,
                )

                # FIXME: should there be some form of recursive application here?
                # if the input string template was source-tagged and the result is not, propagate the source tag to the new value
                if (src_pos := AnsibleSourcePosition.get_tag(variable)) and not AnsibleSourcePosition.is_tagged_on(result):
                    try:
                        result = src_pos.tag(result)
                    except NotTaggableError:
                        pass  # FIXME: determine if there are cases where this error should not be suppressed

                return result

            elif (lazy := _AnsibleLazyTemplateMixin.try_create(variable)) is not None:
                return lazy
            else:
                return variable

    def is_template(self, data):
        '''lets us know if data has a template'''
        if isinstance(data, string_types):
            return is_template(data, self.environment)
        elif isinstance(data, (list, tuple)):
            for v in data:
                if self.is_template(v):
                    return True
        elif isinstance(data, dict):
            for k in data:
                if self.is_template(k) or self.is_template(data[k]):
                    return True
        return False

    templatable = is_template

    def is_possibly_template(self, data, overrides=None):
        data, env, has_override_header = _create_overlay(data, overrides, self.environment)
        return has_override_header or is_possibly_template(data, env)

    def _fail_lookup(self, name, *args, **kwargs):
        raise AnsibleError("The lookup `%s` was found, however lookups were disabled from templating" % name)

    def _query_lookup(self, name, /, *args, **kwargs):
        ''' wrapper for lookup, force wantlist true'''
        kwargs['wantlist'] = True
        return self._lookup(name, *args, **kwargs)

    def _lookup(self, name, /, *args, **kwargs):
        instance = lookup_loader.get(name, loader=self._loader, templar=self)

        if instance is None:
            raise AnsibleError("lookup plugin (%s) not found" % name)

        # some plugins make a poor assumption that `run` takes a list
        args = list(args)

        wantlist = kwargs.pop('wantlist', False)
        allow_unsafe = kwargs.pop('allow_unsafe', C.DEFAULT_ALLOW_UNSAFE_LOOKUPS)
        errors = kwargs.pop('errors', 'strict')

        # safely catch run failures per #5059
        try:
            ran = instance.run(args, variables=self._available_variables, **kwargs)
        except AnsibleUndefinedVariable:
            # this is just to prevent the broad `except Exception` from firing below
            raise
        # FIXME: most of this exception handling should occur at the edge of templating
        except UndefinedError as e:
            raise AnsibleUndefinedVariable(e)
        except AnsibleOptionsError as e:
            # invalid options given to lookup, just reraise
            raise e
        except AnsibleLookupError as e:
            # lookup handled error but still decided to bail
            msg = 'Lookup failed but the error is being ignored: %s' % to_native(e)
            if errors == 'warn':
                display.warning(msg)
            elif errors == 'ignore':
                display.display(msg, log_only=True)
            else:
                raise e
            return [] if wantlist else None
        except Exception as e:
            # errors not handled by lookup
            msg = u"An unhandled exception occurred while running the lookup plugin '%s'. Error was a %s, original message: %s" % \
                  (name, type(e), to_text(e))
            if errors == 'warn':
                display.warning(msg)
            elif errors == 'ignore':
                display.display(msg, log_only=True)
            else:
                display.vvv('exception during Jinja2 execution: {0}'.format(format_exc()))
                raise AnsibleError(to_native(msg), orig_exc=e)
            return [] if wantlist else None

        is_nonstring_sequence = is_sequence(ran)

        if not is_nonstring_sequence:
            display.deprecated(
                f'The lookup plugin \'{name}\' was expected to return a list, got \'{type(ran)}\' instead. '
                f'The lookup plugin \'{name}\' needs to be changed to return a list. '
                'This will be an error in Ansible 2.18',
                version='2.18'
            )

        if ran and allow_unsafe is False:
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
                    raise AnsibleError("The lookup plugin '%s' did not return a list."
                                       % name)
        return ran

    def evaluate_expression(self, expression: str, disable_lookups: bool = False) -> t.Any:
        if not isinstance(expression, str):
            return expression

        if not self._trust_check(expression):
            return expression

        # FIXME: this should ultimately use AnsibleEnvironment.compile_expression() once we've factored all the custom
        #  vars setup into an AnsibleTemplate subclass that TemplateExpression can wrap.
        secret_slug = secrets.token_hex(8)
        block_marker = f'~{secret_slug}~'
        variable_marker = f'<{secret_slug}>'
        comment_marker = f'!{secret_slug}!'
        overrides = dict(
            block_start_string=block_marker,
            block_end_string=block_marker,
            variable_start_string=variable_marker,
            variable_end_string=variable_marker,
            comment_start_string=comment_marker,
            comment_end_string=comment_marker,
        )

        expression_template = TrustedAsTemplate().tag(f'{variable_marker}{expression}{variable_marker}')

        return self.template(expression_template, overrides=overrides, disable_lookups=disable_lookups)

    # FIXME: make allow_inline_template=False by default
    def evaluate_conditional(self, conditional: str, allow_inline_template=True) -> bool:
        if not isinstance(conditional, str):
            # FIXME: this is a change in behavior from devel and needs to be documented
            #        when removing this, be sure to remove the affected test_conditional unit test currently marked xfail
            #        previously, templating could affect truthiness if omit was used, but that isn't something we want to encourage
            #        using "is defined" is a suitable alternative
            #        example:
            #        assert:
            #          that:
            #            - something: "{{ test2_name | default(omit) }}"
            result = conditional
        else:
            # FIXME: this should ultimately use AnsibleEnvironment.compile_expression() once we've factored all the custom
            #  vars setup into an AnsibleTemplate subclass that TemplateExpression can wrap.
            secret_slug = secrets.token_hex(8)
            block_marker = f'~{secret_slug}~'
            variable_marker = f'<{secret_slug}>'
            comment_marker = f'!{secret_slug}!'
            overrides = dict(
                block_start_string=block_marker,
                block_end_string=block_marker,
                variable_start_string=variable_marker,
                variable_end_string=variable_marker,
                comment_start_string=comment_marker,
                comment_end_string=comment_marker,
            )

            if not TrustedAsTemplate.is_tagged_on(conditional):
                raise AnsibleConditionalError(
                    f'Conditional {_repr_from(conditional)} is not trusted. '
                    'Conditionals must be defined by trusted sources such as playbooks, roles, etc., '
                    'and not untrusted sources such as module results.'
                )

            conditional_template = TrustedAsTemplate().tag(f'{variable_marker}{conditional}{variable_marker}')
            escape_backslashes = False  # prevent backslashes from being escaped in the generated template for backwards compatibility

            env_overlay = self.environment.overlay(**overrides)

            try:
                env_overlay.parse(conditional_template)
            except TemplateSyntaxError:
                if not allow_inline_template:
                    raise

                # assume the original conditional was actually a {{ }} style template, process it as such
                conditional_template = conditional
                escape_backslashes = True
                overrides = {}
                display.warning(
                    # FIXME: should we deprecate and/or remove this capability?
                    f'Conditional {_repr_from(conditional)} could not be parsed as a Jinja2 expression, and will be '
                    'evaluated as a template instead. Conditionals should not include templating delimiters '
                    'such as {{ }} or {% %}.'
                )

            try:
                # template the conditional with our overrides specified- any indirect template resolved from vars will be
                # templated with the templar's default environment settings (eg {{ }} var blocks)
                result = self.template(conditional_template, escape_backslashes=escape_backslashes, overrides=overrides)
            except AnsibleUndefinedVariable as e:
                # FIXME: this feels wrong, but we've got so many places that are inconsistently handling/swallowing this error that
                #  at least the warning allows us a place to consistently present useful forensic information about the problem

                conditional_repr = _repr_from(conditional)

                display.warning(f'Conditional {conditional_repr} evaluation failed: {e}')

                raise AnsibleUndefinedVariable(f"error while evaluating conditional {conditional_repr}: {e}") from e

        if isinstance(result, bool):
            return result

        # FIXME: make this a deprecation warning?
        # FIXME: include location info?
        bool_result = bool(result)
        # FIXME: `type(result)` should probably be the base type of the data structure
        # FIXME: add an option to make these errors, enabled by default for integration tests
        display.warning(f'Conditional {_repr_from(conditional)} had result {result!r} of type {type(result)}, '
                        f'which evaluates to {bool_result}. Conditionals should always have a boolean result.')

        return bool_result

    @staticmethod
    def _trust_check(data: str) -> bool:
        """
        Return True if the given template data is trusted for templating, otherwise return False.

        Emits a warning if the data is not trusted, unless it was tagged with `NotATemplate`.
        """
        if NotATemplate.is_tagged_on(data):
            return False

        if not TrustedAsTemplate.is_tagged_on(data):
            # display.warning(f'skipped untrusted template {data=}')
            from traceback import format_stack

            # FIXME: make traceback optional
            tb = "\n".join(format_stack())
            display.warning(f'skipped untrusted template {_repr_from(data)}; execution stack:\n{tb}')

            return False

        return True

    def _detonate_vault_bombs(self, value: t.Any) -> None:
        if type(value) is _AnsibleTaggedVaultBomb:  # pylint: disable=unidiomatic-typecheck
            value.detonate()
        elif is_sequence(value):
            for x in value:
                self._detonate_vault_bombs(x)
        elif isinstance(value, Mapping):
            # FIXME: any worry about keys?
            for x in value.values():
                self._detonate_vault_bombs(x)

    def do_template(self, data, *, undefined_behavior, preserve_trailing_newlines=True, escape_backslashes=True, fail_on_undefined=None, overrides=None,
                    disable_lookups=False):
        if not TemplateContext.current():
            # FIXME: deprecation? Also, probably include a stacktrace...
            display.warning('missing TemplateContext (direct call to do_template?)')

        # FIXME: FDI013
        if not isinstance(data, str):
            return data

        if not self._trust_check(data):
            return data

        # For preserving the number of input newlines in the output (used
        # later in this method)
        data_newlines = _count_newlines_from_end(data)

        if fail_on_undefined is None:
            fail_on_undefined = self._fail_on_undefined_errors

        try:
            # NOTE Creating an overlay that lives only inside do_template means that overrides are not applied
            # when templating nested variables in AnsibleJ2Vars where Templar.environment is used, not the overlay.
            data, myenv, _has_override_header = _create_overlay(data, overrides, self.environment, undefined_behavior=undefined_behavior)

            if escape_backslashes:
                data = _escape_backslashes(data, myenv)

            try:
                cur_template = myenv.from_string(data)
            except (TemplateSyntaxError, SyntaxError) as e:
                raise AnsibleError("template error while templating string: %s. String: %s" % (to_native(e), to_native(data)), orig_exc=e)
            except Exception as e:
                if 'recursion' in to_native(e):
                    raise AnsibleError("recursive loop detected in template string: %s" % to_native(data), orig_exc=e)
                else:
                    return data

            if disable_lookups:
                cur_template.globals['query'] = cur_template.globals['q'] = cur_template.globals['lookup'] = self._fail_lookup

            jvars = AnsibleJ2Vars(self, cur_template.globals)

            cur_context = cur_template.new_context(jvars, shared=True)

            rf = cur_template.root_render_func(cur_context)

            try:
                res = myenv.concat(rf)
                # FIXME: propagate some/all tags here?
            except TypeError as te:
                if 'AnsibleUndefined' in to_native(te):
                    errmsg = "Unable to look up a name or access an attribute in template string (%s).\n" % to_native(data)
                    errmsg += "Make sure your variable name does not contain invalid characters like '-': %s" % to_native(te)
                    raise AnsibleUndefinedVariable(errmsg, orig_exc=te)
                else:
                    display.debug("failing because of a type error, template data is: %s" % to_text(data))
                    raise AnsibleError("Unexpected templating type error occurred on (%s): %s" % (to_native(data), to_native(te)), orig_exc=te)

            if isinstance(res, string_types) and preserve_trailing_newlines:
                # The low level calls above do not preserve the newline
                # characters at the end of the input data, so we use the
                # calculate the difference in newlines and append them
                # to the resulting output for parity
                #
                # Using AnsibleEnvironment's keep_trailing_newline instead would
                # result in change in behavior when trailing newlines
                # would be kept also for included templates, for example:
                # "Hello {% include 'world.txt' %}!" would render as
                # "Hello world\n!\n" instead of "Hello world!\n".
                res_newlines = _count_newlines_from_end(res)
                if data_newlines > res_newlines:
                    newlines = self.environment.newline_sequence * (data_newlines - res_newlines)
                    res = AnsibleTaggedObject.tag_copy(res, res + newlines)
            return res
        except Exception:
            # FIXME: lazy testing, remove this whole thing once we've centralized the handling of these errors
            raise
        # except (UndefinedError, AnsibleUndefinedVariable) as e:
        #     if fail_on_undefined:
        #         raise AnsibleUndefinedVariable(e, orig_exc=e)
        #     else:
        #         display.debug("Ignoring undefined failure: %s" % to_text(e))
        #         return data

    # for backwards compatibility in case anyone is using old private method directly
    _do_template = do_template


# FIXME: add tests to ensure this doesn't drift from allowed types
def _finalize_template_result(o: t.Any, undefined_behavior: UndefinedBehavior, raise_on_unsupported_type: bool) -> t.Any:
    """
    Recurse the template result, rendering any encountered templates, converting containers to non-lazy versions.
    """
    o_type = type(o)

    from ansible.vars.hostvars import HostVars, HostVarsVars  # FIXME: really bad idea, don't do this -- this is here just to see if the tests pass otherwise

    value_type: type[dict | list | tuple | set]

    if o_type in _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES:
        return o
    elif o_type in (dict, _AnsibleTaggedDict, _AnsibleLazyTemplateDict):
        value_expression = (_finalize_template_result((k, v), undefined_behavior, raise_on_unsupported_type) for k, v in o.items() if v is not Omit)
        value_type = dict
    elif o_type in (list, _AnsibleTaggedList, _AnsibleLazyTemplateList):
        value_expression = (_finalize_template_result(v, undefined_behavior, raise_on_unsupported_type) for v in o if v is not Omit)
        value_type = list
    elif o_type in (tuple, _AnsibleTaggedTuple, _AnsibleLazyTemplateTuple):
        value_expression = (_finalize_template_result(v, undefined_behavior, raise_on_unsupported_type) for v in o if v is not Omit)
        value_type = tuple
    elif o_type in (set, _AnsibleTaggedSet, _AnsibleLazyTemplateSet):
        value_expression = (_finalize_template_result(v, undefined_behavior, raise_on_unsupported_type) for v in o if v is not Omit)
        value_type = set
    elif o_type is AnsibleUndefined:
        return undefined_behavior.handle_undefined(o)  # FIXME: this assumes handle_undefined follows our variable type rules
    elif o_type in (HostVars, HostVarsVars):
        return o  # FIXME: really bad idea, don't do this -- this is here just to see if the tests pass otherwise
    elif raise_on_unsupported_type:  # unsupported type (raise)
        if o_type is _AnsibleTaggedVaultBomb:
            o.detonate()

        raise AnsibleVariableTypeError(variable_type=o_type)
    else:  # unsupported type (do not raise)
        return o

    return AnsibleTaggedObject.tag_copy(o, value_expression, value_type=value_type)


# FIXME: decide if these should be taggable; do we need to support other kinds of Undefineds, etc
from ansible.module_utils import datatag

datatag._untaggable_types |= {AnsibleUndefined, type(Omit)}
