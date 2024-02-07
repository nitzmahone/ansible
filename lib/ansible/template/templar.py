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

import ast
import dataclasses

import ansible.module_utils.compat.typing as t

from collections.abc import Mapping
from contextlib import contextmanager
from traceback import format_exc, format_stack
from collections import ChainMap

from jinja2.exceptions import TemplateSyntaxError, UndefinedError
from jinja2.loaders import FileSystemLoader
from jinja2.environment import TemplateExpression
from jinja2 import __version__ as jinja2_version

from ansible import constants as C
from ansible.errors import (
    AnsibleAssertionError,
    AnsibleError,
    AnsibleLookupError,
    AnsibleValueOmittedError,
    AnsibleOptionsError,
    AnsibleUndefinedVariable,
    AnsibleTemplateError,
    AnsibleTemplateSyntaxError,
)
from ansible.module_utils.common.text.converters import to_native, to_text
from ansible.module_utils.common.collections import is_sequence
from ansible.plugins.loader import lookup_loader
from ansible.module_utils.datatag import (
    AnsibleSourcePosition, AnsibleTaggedObject, TrustedAsTemplate, NotATemplate, NotTaggableError, _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES,
)
from ansible.module_utils.datatag.access import AnsibleAccessContext

from ansible.utils.display import Display
from ansible.utils.vars import isidentifier

from .datatag import DeprecatedAccessAuditContext
from .jinja_bits import AnsibleEnvironment, AnsibleTemplate, _TemplateCompileContext
from .vault import DetonateVaultBombsTripwire, UndecryptableAccessMutator
from .utils import Omit, TemplateContext, TemplateDepthContext
from .lazy_containers import _AnsibleLazyTemplateMixin, _finalize_template_result
from .undefined_behaviors import FAIL_ON_UNDEFINED, UndefinedBehavior

_display = Display()

_JINJA2_OVERRIDE = '#jinja2:'
_JINJA2_BEGIN_TOKENS = frozenset(('variable_begin', 'block_begin', 'comment_begin', 'raw_begin'))
_JINJA2_END_TOKENS = frozenset(('variable_end', 'block_end', 'comment_end', 'raw_end'))

# FIXME: remove/harden- just here for development backstop for now
if tuple(map(int, jinja2_version.split('.'))) < (3, 1):
    raise RuntimeError('Jinja 3.1+ required')


# FIXME: do we still need a class for this?
@dataclasses.dataclass(frozen=True, kw_only=True, slots=True)
class TemplateResult:
    result: t.Any

    def as_text(self):
        result = self.result
        return AnsibleTaggedObject.tag(str(result), AnsibleTaggedObject.tags(result) | {NotATemplate()})


class TemplateTrustCheckFailedError(AnsibleTemplateError):
    pass


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TemplateOptions:
    # FIXME: embedded sentinel
    preserve_trailing_newlines: bool = t.cast(bool, ...)
    escape_backslashes: bool = t.cast(bool, ...)
    overrides: dict[str, t.Any] | None = t.cast(None, ...)
    disable_lookups: bool = t.cast(bool, ...)
    undefined_behavior: UndefinedBehavior = t.cast(UndefinedBehavior, ...)
    stop_on_container_result: bool = t.cast(bool, ...)
    value_for_omit: t.Any = ...

    def __post_init__(self):
        if template_ctx := TemplateDepthContext.current():
            if self.stop_on_container_result is not ...:
                raise ValueError("stop_on_container_result is only valid for top-level template calls.")
            if self.value_for_omit is not ...:
                raise ValueError("value_for_omit is only valid for top-level template calls.")
            defaults = template_ctx.options
        else:
            try:
                defaults = _DEFAULT_TEMPLATE_OPTIONS
            except NameError:
                # HACK: stop post_init here when creating the shared defaults
                return

        # FIXME: dataclasses.replace on the defaults in a factory?
        for field in dataclasses.fields(self):
            # FIXME: tighten this up?
            if getattr(self, field.name) is ...:
                # FIXME: figure out a better way to avoid propagating options
                # FIXME: review all options to determine correct propagation behavior
                if field.name in ('stop_on_container_result', 'value_for_omit', 'overrides', 'escape_backslashes'):
                    value = getattr(_DEFAULT_TEMPLATE_OPTIONS, field.name)
                else:
                    value = getattr(defaults, field.name)

                object.__setattr__(self, field.name, value)


_DEFAULT_TEMPLATE_OPTIONS: t.Final = TemplateOptions(
    preserve_trailing_newlines=True,
    escape_backslashes=True,
    overrides=None,
    disable_lookups=False,
    undefined_behavior=FAIL_ON_UNDEFINED,
    stop_on_container_result=False,
    value_for_omit=Omit,
)


class Templar:
    """
    The main class for templating, with the main entry-point of template().
    """

    # allow unit tests to easily patch trust check failures to raise instead of just warn
    _raise_on_trust_check_fail = False
    _sentinel = object()

    def __init__(self, loader, variables=None):
        self._loader = loader
        self._available_variables = {} if variables is None else variables

        self.environment = AnsibleEnvironment(
            extensions=self._get_extensions(),
            loader=FileSystemLoader(loader.get_basedir() if loader else '.'),
        )

        # FIXME: move all this magic under our Jinja environment?

        # Custom globals
        self.environment.globals['lookup'] = self._lookup
        self.environment.globals['query'] = self.environment.globals['q'] = self._query_lookup

    @staticmethod
    def _repr_from(value: t.Any) -> str:
        """Return the repr() of the given value, appending attribution of the source position, if available."""
        # FIXME: FDI028 - initial prototype, is this what we want?
        #        should it be part of our public interface?
        #        should this be part of AnsibleSourcePosition or otherwise in the datatag module_utils?

        # FIXME: need to elide container values and large strings
        src_pos = AnsibleSourcePosition.get_tag(value)

        if src_pos:
            return f'{value!r} from {str(src_pos)!r}'

        return f'{value!r}'

    @staticmethod
    def _is_possibly_template_internal(data, jinja_env):
        """Determines if a string looks like a template, by seeing if it
        contains a jinja2 start delimiter. Does not guarantee that the string
        is actually a template.

        This is different than ``is_template`` which is more strict.
        This method may return ``True`` on a string that is not templatable.

        Useful when guarding passing a string for templating, but when
        you want to allow the templating engine to make the final
        assessment which may result in ``TemplateSyntaxError``.
        """
        if isinstance(data, str):
            for marker in (jinja_env.block_start_string, jinja_env.variable_start_string, jinja_env.comment_start_string):
                if marker in data:
                    return True
        return False

    def _is_template_internal(self, data):
        """This function attempts to quickly detect whether a value is a jinja2
        template. To do so, we look for the first 2 matching jinja2 tokens for
        start and end delimiters.
        """
        found = None
        start = True
        comment = False
        env = self.environment
        d2 = env.preprocess(data)

        # Quick check to see if this is remotely like a template before doing
        # more expensive investigation.
        if not self._is_possibly_template_internal(d2, env):
            return False

        # This wraps a lot of code, but this is due to lex returning a generator
        # so we may get an exception at any part of the loop
        try:
            for token in env.lex(d2):
                if token[1] in _JINJA2_BEGIN_TOKENS:
                    if start and token[1] == 'comment_begin':
                        # Comments can wrap other token types
                        comment = True
                    start = False
                    # Example: variable_end -> variable
                    found = token[1].split('_')[0]
                elif token[1] in _JINJA2_END_TOKENS:
                    if token[1].split('_')[0] == found:
                        return True
                    elif comment:
                        continue
                    return False
        except TemplateSyntaxError:
            return False

        return False

    def _create_overlay(self, data: str, overrides: dict) -> tuple[str, AnsibleEnvironment, bool]:
        if overrides is None:
            overrides = {}

        try:
            has_override_header = data.startswith(_JINJA2_OVERRIDE)
        except (TypeError, AttributeError):
            has_override_header = False

        if overrides or has_override_header:
            overlay = self.environment.overlay(**overrides)
        else:
            overlay = self.environment

        # Get jinja env overrides from template
        if has_override_header:
            eol = data.find('\n')
            line = data[len(_JINJA2_OVERRIDE):eol]
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
                    _display.warning(f"Could not find Jinja2 environment setting to override: '{key}'")

        return data, overlay, has_override_header

    @staticmethod
    def _count_newlines_from_end(in_str):
        """
        Counts the number of newlines at the end of a string. This is used during
        the jinja2 templating to ensure the count matches the input, since some newlines
        may be thrown away during the templating.
        """

        i = len(in_str)
        j = i - 1

        try:
            while in_str[j] == '\n':
                j -= 1
        except IndexError:
            # Uncommon cases: zero length string and string containing only newlines
            return i

        return i - 1 - j

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
        """
        Return jinja2 extensions to load.

        If some extensions are set via jinja_extensions in ansible.cfg, we try
        to load them with the jinja environment.
        """

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
        """
        Sets the list of template variables this Templar instance will use
        to template things, so we don't have to pass them around between
        internal methods.
        """

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
        return self.evaluate_expression(TrustedAsTemplate().tag(stripped_expression))

    # FIXME: implement a pylint check for proper usage of LiteralString args (even if mypy eventually supports it,
    # we'll want this to get checked for collections, too).
    def template_literal_expression(self, expression: t.LiteralString, var_overrides: dict[str, t.Any] | None = None) -> t.Any:
        """Template string literal expressions with blind trust."""
        # FIXME: propagate other tags? (source position, etc)
        variables = ChainMap(var_overrides, self._available_variables) if var_overrides else self._available_variables
        templar = Templar(self._loader, variables=variables)
        return templar.evaluate_expression(TrustedAsTemplate().tag(expression))

    def variable_name_as_template(self, name: str) -> str:
        stripped_name = name.strip()
        if not isidentifier(stripped_name):
            # FIXME: better exception type here
            raise AnsibleError(f"invalid variable name: {stripped_name}")
        # FIXME: propagate other tags? (source position, etc)
        # this is safe enough to blindly apply trust, since it can only be an identifier
        return TrustedAsTemplate().tag('{{' + stripped_name + '}}')

    # FIXME: wrap tripwires in a template decorator so we can preserve/propagate args automatically
    def template(self, variable: t.Any, *, options: TemplateOptions | None = None) -> t.Any:
        return self.template_with_result(variable, options=options).result

    # FIXME: expression_mode should be strings only- enforce (or use a different entrypoint)
    def template_with_result(self, variable: t.Any, *, options: TemplateOptions | None = None, expression_mode=False) -> TemplateResult:
        """Templates (possibly recursively) any given data as input."""

        # bail out if we know we're looking at something that's been explicitly tagged as not a template
        if variable is None or NotATemplate.is_tagged_on(variable):
            return TemplateResult(result=variable)  # input was not manipulated, trust that it contains only allowed types

        # FIXME: early exit on empty collections

        # FIXME: tighten this up, and figure out a better way to avoid propagating options
        if template_depth_ctx := TemplateDepthContext.current():
            # FIXME: ideally avoid re-creating TemplateOptions every time here
            options = options or TemplateOptions()
        else:
            options = options or _DEFAULT_TEMPLATE_OPTIONS

        is_top_level_template = not template_depth_ctx

        # track access to items that are tagged Deprecated during templating, handle accordingly
        with (
                UndecryptableAccessMutator(),  # trigger injection of VaultBomb
                DeprecatedAccessAuditContext() as deprecated,
                TemplateDepthContext(options=options),
        ):
            try:
                # stack the current active var value we're templating; this lets the deprecated tripwire ask for it
                with TemplateContext(template_value=variable, templar=self):
                    if not isinstance(variable, str):
                        if options.overrides is not None:
                            raise ValueError("Jinja overrides are only allowed on string inputs")

                        template_result = _AnsibleLazyTemplateMixin.try_create(variable)
                    elif not expression_mode and not self.is_possibly_template(variable, options.overrides):
                        template_result = variable
                    elif not self._trust_check(variable, expression_mode=expression_mode):
                        template_result = variable
                    else:
                        if expression_mode:
                            compiled_expression = self._compile_expression(variable, options)
                            template_result = compiled_expression(self.available_variables)
                        else:
                            compiled_template = self._compile_template(variable, options)

                            template_result = compiled_template.render(self.available_variables)

                        template_result = self._post_render_mutation(variable, template_result, options)

                # If we're the outermost template operation, we need to recursively finalize the template result.
                # This will render any embedded templates and trigger undefined, omit and vault bomb behaviors.
                if is_top_level_template:
                    if template_result is Omit:
                        if options.value_for_omit is Omit:
                            raise AnsibleValueOmittedError()

                        return TemplateResult(result=options.value_for_omit)  # value_for_omit was not manipulated, trust that it contains only allowed types

                    if options.stop_on_container_result and type(template_result) in _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES:
                        # Use of stop_on_container_result implies the caller will perform necessary checks on values,
                        # most likely by passing them back into the templating system.
                        return TemplateResult(
                            result=template_result.native_copy() if template_result in AnsibleTaggedObject._collection_types else template_result,
                        )

                    # data is our only positional arg, everything else is kwargs-only
                    with DetonateVaultBombsTripwire():
                        template_result = _finalize_template_result(template_result, raise_on_unsupported_type=True)
                        template_result = options.undefined_behavior.post_finalize(template_result)
            except Exception as ex:
                self._raise_template_error(ex, variable)

        self._emit_deprecation_warnings(deprecated)

        return TemplateResult(result=template_result)

    def _compile_template(self, template: str, options: TemplateOptions) -> AnsibleTemplate:
        # NOTE: Creating an overlay that lives only inside _do_template means that overrides are not applied
        # when templating nested variables, where Templar.environment is used, not the overlay.
        stripped_template, env, _has_override_header = self._create_overlay(template, options.overrides)

        with _TemplateCompileContext(escape_backslashes=options.escape_backslashes):
            compiled_template = t.cast(AnsibleTemplate, env.from_string(stripped_template))

        if options.disable_lookups:
            compiled_template.globals['query'] = compiled_template.globals['q'] = compiled_template.globals['lookup'] = self._fail_lookup

        return compiled_template

    def _compile_expression(self, expression: str, options: TemplateOptions | None = None) -> TemplateExpression:
        """
        Compile a Jinja expression, applying optional compile-time behavior via an environment overlay (if needed). The overlay is
        necessary to avoid mutating settings on the Templar's shared environment, which could be visible to other code running concurrently.
        In the specific case of escape_backslashes, the setting only applies to a top-level template at compile-time, not runtime, to
        ensure that any nested template calls (e.g., include and import) do not inherit the (lack of) escaping behavior.
        """
        # FIXME: disable_lookups not supported here?

        with _TemplateCompileContext(escape_backslashes=options.escape_backslashes):
            return self.environment.compile_expression(expression, False)

    def _post_render_mutation(self, template: str, result: t.Any, options: TemplateOptions) -> t.Any:
        if options.preserve_trailing_newlines and isinstance(result, str):
            # The low level calls above do not preserve the newline
            # characters at the end of the input data, so we
            # calculate the difference in newlines and append them
            # to the resulting output for parity
            #
            # Using AnsibleEnvironment's keep_trailing_newline instead would
            # result in change in behavior when trailing newlines
            # would be kept also for included templates, for example:
            # "Hello {% include 'world.txt' %}!" would render as
            # "Hello world\n!\n" instead of "Hello world!\n".
            data_newlines = self._count_newlines_from_end(template)
            res_newlines = self._count_newlines_from_end(result)

            if data_newlines > res_newlines:
                newlines = self.environment.newline_sequence * (data_newlines - res_newlines)
                result = AnsibleTaggedObject.tag_copy(result, result + newlines)

        # FIXME: ensure tag propagation behavior is working for containers
        # FIXME: should there be some form of recursive application here?
        # FIXME: propagate more tags here?
        # if the input string template was source-tagged and the result is not, propagate the source tag to the new value
        if (src_pos := AnsibleSourcePosition.get_tag(template)) and not AnsibleSourcePosition.is_tagged_on(result):
            try:
                result = src_pos.tag(result)
            except NotTaggableError:
                pass  # FIXME: determine if there are cases where this error should not be suppressed

        return result

    def _emit_deprecation_warnings(self, deprecated: DeprecatedAccessAuditContext) -> None:
        # FIXME: create a dataclass or something for runtime capture of deprecation info plus the template context the access occurred in
        for deprecation_template, deprecation in deprecated.deprecated_access:
            # FIXME: if we're in a worker, propagate deprecated access warnings back to the controller for deduplication
            # FIXME: the current template may not have a source position, we may need to consult a parent template
            _display.deprecated(
                msg=f'{deprecation.msg} while templating {self._repr_from(deprecation_template)}',
                version=deprecation.removal_version,
                date=deprecation.removal_date,
            )

    @staticmethod
    def _raise_template_error(ex: Exception, variable: t.Any) -> t.NoReturn:
        # FIXME: capture useful context information from each context early

        if isinstance(ex, AnsibleTemplateError):
            exception_to_raise = ex
        else:
            src_pos = AnsibleSourcePosition.get_tag(variable)

            if isinstance(variable, str):
                cause = repr(variable)
            else:
                cause = f'of type {type(variable)}'

            if src_pos:
                cause += f'from {src_pos}'

            ex_type = AnsibleTemplateError  # always raise an AnsibleTemplateError/subclass
            if isinstance(ex, RecursionError):
                msg = f"Recursive loop detected in template {cause}"
            elif isinstance(ex, TemplateSyntaxError):
                msg = f"Syntax error in template {cause}: {ex}"
                ex_type = AnsibleTemplateSyntaxError
            else:
                msg = f"Unexpected exception rendering template {cause}: {ex}"

            exception_to_raise = ex_type(NotATemplate().tag(msg), orig_exc=ex)

        # FIXME: apply captured context information from above onto `exception_to_raise` here, before (re)raising

        if exception_to_raise is ex:
            raise  # pylint: disable=misplaced-bare-raise

        raise exception_to_raise from ex

    def is_template(self, data):
        """lets us know if data has a template"""
        if isinstance(data, str):
            return self._is_template_internal(data)
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
        data, env, has_override_header = self._create_overlay(data, overrides)
        return has_override_header or self._is_possibly_template_internal(data, env)

    def _fail_lookup(self, name, *args, **kwargs):
        raise AnsibleError("The lookup `%s` was found, however lookups were disabled from templating" % name)

    def _query_lookup(self, name, /, *args, **kwargs):
        """wrapper for lookup, force wantlist true"""
        kwargs['wantlist'] = True
        return self._lookup(name, *args, **kwargs)

    def _lookup(self, name, /, *args, **kwargs):
        # FIXME: we should probably be running the result of lookup plugins through proxy_or_render_template

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
                _display.warning(msg)
            elif errors == 'ignore':
                _display.display(msg, log_only=True)
            else:
                raise e
            return [] if wantlist else None
        except Exception as e:
            # errors not handled by lookup
            msg = u"An unhandled exception occurred while running the lookup plugin '%s'. Error was a %s, original message: %s" % \
                  (name, type(e), to_text(e))
            if errors == 'warn':
                _display.warning(msg)
            elif errors == 'ignore':
                _display.display(msg, log_only=True)
            else:
                _display.vvv('exception during Jinja2 execution: {0}'.format(format_exc()))
                raise AnsibleError(to_native(msg), orig_exc=e)
            return [] if wantlist else None

        is_nonstring_sequence = is_sequence(ran)

        if not is_nonstring_sequence:
            _display.deprecated(
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

    def evaluate_expression(self, expression: str, disable_lookups: bool = False, escape_backslashes=True) -> t.Any:
        if not isinstance(expression, str):
            raise TypeError(f"evaluate_expression requires {str!r}, got {type(expression)!r}")

        return self.template_with_result(
            expression,
            options=TemplateOptions(disable_lookups=disable_lookups, escape_backslashes=escape_backslashes),
            expression_mode=True,
        ).result

    # FIXME: make allow_inline_template=False by default?
    def evaluate_conditional(self, conditional: str, allow_inline_template=True) -> bool:
        try:
            result = self._sentinel

            if isinstance(conditional, str):
                try:
                    # Disable escape_backslashes when processing conditionals, to maintain backwards compatibility.
                    # This is necessary because conditionals were previously evaluated using {% %}, which was *NOT* affected by escape_backslashes.
                    # Now that conditionals use expressions, they would be affected by escape_backslashes if it was not disabled.
                    result = self.evaluate_expression(conditional, escape_backslashes=False)
                except AnsibleTemplateSyntaxError:
                    if not allow_inline_template or not self.is_template(conditional):
                        raise

            elif not allow_inline_template:
                # FIXME: mention "and allow_inline_template=False" when we figure out what we want
                raise TypeError(f"evaluate_conditional requires {str!r}, got {type(conditional)!r}")

            if result is self._sentinel:
                _display.warning(
                    # FIXME: should we deprecate and/or remove this capability?
                    f'Conditional {self._repr_from(conditional)} could not be parsed as a Jinja2 expression, and will be '
                    'evaluated as a template instead. Conditionals should not include templating delimiters '
                    'such as {{ }} or {% %}.'
                )
                result = self.template(conditional)
        except AnsibleUndefinedVariable as e:
            # FIXME: this feels wrong, but we've got so many places that are inconsistently handling/swallowing this error that
            #  at least the warning allows us a place to consistently present useful forensic information about the problem

            conditional_repr = self._repr_from(conditional)

            _display.warning(f'Conditional {conditional_repr} evaluation failed: {e}')

            raise AnsibleUndefinedVariable(f"error while evaluating conditional {conditional_repr}: {e}") from e

        if isinstance(result, bool):
            return result

        # FIXME: make this a deprecation warning?
        # FIXME: include location info?
        bool_result = bool(result)
        # FIXME: `type(result)` should probably be the base type of the data structure
        # FIXME: add an option to make these errors, enabled by default for integration tests
        _display.warning(f'Conditional {self._repr_from(conditional)} had result {result!r} of type {type(result)}, '
                         f'which evaluates to {bool_result}. Conditionals should always have a boolean result.')

        return bool_result

    def _trust_check(self, data: str, expression_mode: bool = False) -> bool:
        """
        Return True if the given template data is trusted for templating, otherwise return False.

        Emits a warning if the data is not trusted, unless it was tagged with `NotATemplate`.
        """
        if NotATemplate.is_tagged_on(data):
            return False

        if not TrustedAsTemplate.is_tagged_on(data):
            if Templar._raise_on_trust_check_fail or expression_mode:
                thing = "expression" if expression_mode else "template"
                raise TemplateTrustCheckFailedError(f'Failing on untrusted {thing} {self._repr_from(data)}. '
                                                    f'Expressions and templates must be defined by trusted sources such as playbooks, roles, etc., '
                                                    'and not untrusted sources such as module results.')

            # FIXME: make traceback optional
            tb = "\n".join(format_stack())
            _display.warning(f'skipped untrusted template {self._repr_from(data)}; execution stack:\n{tb}')

            return False

        return True

    def proxy_or_render_template(self, item: t.Any, key: str | None = None):
        # FIXME: always blindly access item here?
        # FIXME: should we do something with key here, or remove it?
        return self.template(AnsibleAccessContext.current().access(item))
