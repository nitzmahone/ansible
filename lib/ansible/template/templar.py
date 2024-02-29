# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import collections.abc as c
import dataclasses
import enum
import os

import ansible.module_utils.compat.typing as t

from contextlib import contextmanager
from traceback import format_exc, format_stack
from collections import ChainMap

from jinja2.exceptions import TemplateSyntaxError, UndefinedError
from jinja2.loaders import FileSystemLoader
from jinja2 import __version__ as jinja2_version

from ansible import constants as C
from ansible.errors import (
    AnsibleError,
    AnsibleLookupError,
    AnsibleValueOmittedError,
    AnsibleOptionsError,
    AnsibleUndefinedVariable,
    AnsibleTemplateError,
    AnsibleTemplateSyntaxError,
    AnsibleBrokenConditionalError, AnsibleTemplatePluginNotFoundError,
)
from ansible.module_utils.common.text.converters import to_native, to_text
from ansible.module_utils.common.collections import is_sequence
from ansible.plugins.loader import lookup_loader
from ansible.module_utils.datatag import (
    AnsibleSourcePosition, AnsibleTaggedObject, TrustedAsTemplate, NotATemplate, NotTaggableError
)
from ansible.module_utils.datatag.access import AnsibleAccessContext

from ansible.utils.display import Display
from ansible.utils.vars import isidentifier
from ansible.parsing.dataloader import DataLoader

from .datatag import DeprecatedAccessAuditContext, _RenderJinjaConstAsTemplate
from .jinja_bits import AnsibleEnvironment, AnsibleTemplate, _TemplateCompileContext, TemplateOverrides, \
    _TEMPLATE_OVERRIDE_DEFAULT, is_possibly_template, is_possibly_all_template, AnsibleTemplateExpression, _finalize_template_result, FinalizeMode
from .vault import DetonateVaultBombsTripwire, UndecryptableAccessMutator
from .utils import Omit, TemplateContext, _repr_from
from .lazy_containers import _AnsibleLazyTemplateMixin
from .undefined_behaviors import FAIL_ON_UNDEFINED, UndefinedBehavior

_display = Display()

# FIXME: remove/harden- just here for development backstop for now
if tuple(map(int, jinja2_version.split('.'))) < (3, 1):
    raise RuntimeError('Jinja 3.1+ required')


def as_non_templatable_text(value: t.Any) -> str:
    return AnsibleTaggedObject.tag(str(value), AnsibleTaggedObject.tags(value) | {NotATemplate()})


class TemplateTrustCheckFailedError(AnsibleTemplateError):
    pass


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TemplateOptions:
    # FIXME: embedded sentinel
    preserve_trailing_newlines: bool = t.cast(bool, ...)
    escape_backslashes: bool = t.cast(bool, ...)
    overrides: TemplateOverrides = t.cast(TemplateOverrides, ...)  # FIXME: these aren't really overrides anymore, rename the dataclass and this field
    undefined_behavior: UndefinedBehavior = t.cast(UndefinedBehavior, ...)
    value_for_omit: t.Any = ...

    def __post_init__(self):
        if template_ctx := TemplateContext.current():
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
                if field.name in ('value_for_omit', 'overrides', 'escape_backslashes'):
                    value = getattr(_DEFAULT_TEMPLATE_OPTIONS, field.name)
                else:
                    value = getattr(defaults, field.name)

                object.__setattr__(self, field.name, value)


_DEFAULT_TEMPLATE_OPTIONS: t.Final = TemplateOptions(
    preserve_trailing_newlines=True,
    escape_backslashes=True,
    overrides=_TEMPLATE_OVERRIDE_DEFAULT,
    undefined_behavior=FAIL_ON_UNDEFINED,
    value_for_omit=Omit,
)


class TemplateEncountered(Exception):
    pass


class TemplateMode(enum.Enum):
    DEFAULT = enum.auto()
    EXPRESSION = enum.auto()
    STOP_ON_TEMPLATE = enum.auto()
    STOP_ON_CONTAINER = enum.auto()


class Templar:
    """
    The main class for templating, with the main entry-point of template().
    """

    # allow unit tests to easily patch trust check failures to raise instead of just warn
    _raise_on_trust_check_fail = False
    _sentinel = object()
    _allow_broken_conditionals = C.config.get_config_value('ALLOW_BROKEN_CONDITIONALS')
    _jinja_extensions = C.config.get_config_value('DEFAULT_JINJA2_EXTENSIONS')

    def __init__(
        self,
        loader: DataLoader | None = None,
        variables: dict[str, t.Any] | ChainMap[str, t.Any] | None = None,
        variables_factory: t.Callable[[], dict[str, t.Any] | ChainMap[str, t.Any]] | None = None,
    ):
        self._loader = loader
        self._variables = variables
        self._variables_factory = variables_factory
        self._environment: AnsibleEnvironment | None = None

    @property
    def basedir(self) -> str:
        return self._loader.get_basedir() if self._loader else '.'

    @property
    def environment(self) -> AnsibleEnvironment:
        if not self._environment:
            env = AnsibleEnvironment(
                extensions=self._jinja_extensions,
                loader=FileSystemLoader(self.basedir),
            )

            env.globals.update(
                lookup=self._lookup,
                query=self._query_lookup,
                q=self._query_lookup,
            )

            self._environment = env

        return self._environment

    def _create_overlay(self, template: str, overrides: TemplateOverrides) -> tuple[str, AnsibleEnvironment]:
        try:
            template, overrides = overrides.extract_template_overrides(template)
        except (TypeError, ValueError) as ex:
            raise AnsibleTemplateSyntaxError(str(ex)) from ex

        env = self.environment

        if overrides is not _TEMPLATE_OVERRIDE_DEFAULT and (overlay_kwargs := overrides.overlay_kwargs()):
            env = t.cast(AnsibleEnvironment, env.overlay(**overlay_kwargs))

        return template, env

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

    @property
    def available_variables(self) -> dict[str, t.Any] | ChainMap[str, t.Any]:
        if self._variables is None:
            self._variables = self._variables_factory() if self._variables_factory else {}

        return self._variables

    @available_variables.setter
    def available_variables(self, variables: dict[str, t.Any]) -> None:
        """
        Sets the list of template variables this Templar instance will use
        to template things, so we don't have to pass them around between
        internal methods.
        """
        self._variables = variables

    @contextmanager
    def set_temporary_context(
        self,
        searchpath: t.Union[str, os.PathLike, t.Sequence[t.Union[str, os.PathLike]]] | None = None,
        available_variables: dict[str, t.Any] | ChainMap[str, t.Any] | None = None,
    ) -> t.Generator[None, None, None]:
        """Context manager used to set temporary templating context, without having to worry about resetting original values afterward."""
        env = self.environment

        targets = dict(
            available_variables=self,
            searchpath=env.loader,
        )

        kwargs = dict(
            searchpath=searchpath,
            available_variables=available_variables,
        )

        original: dict[str, t.Any] = {}

        for key, value in kwargs.items():
            if value is not None:
                target = targets[key]
                original[key] = getattr(target, key)
                setattr(target, key, value)

        try:
            yield
        finally:
            for key, value in original.items():
                setattr(targets[key], key, value)

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
        variables = ChainMap(var_overrides, self.available_variables) if var_overrides else self.available_variables
        templar = Templar(self._loader, variables=variables)
        return templar.evaluate_expression(TrustedAsTemplate().tag(expression))

    @staticmethod
    def variable_name_as_template(name: str) -> str:
        stripped_name = name.strip()
        if not isidentifier(stripped_name):
            # FIXME: better exception type here
            raise AnsibleError(f"invalid variable name: {stripped_name}")
        # FIXME: propagate other tags? (source position, etc)
        # this is safe enough to blindly apply trust, since it can only be an identifier
        return TrustedAsTemplate().tag('{{' + stripped_name + '}}')

    # FIXME: TemplateMode.EXPRESSION should be strings only- enforce (or use a different entrypoint)
    def template(
            self,
            variable: t.Any,
            *,
            options: TemplateOptions | None = None,
            mode: TemplateMode = TemplateMode.DEFAULT,
    ) -> t.Any:
        """Templates (possibly recursively) any given data as input."""

        # bail out if we know we're looking at something that's been explicitly tagged as not a template
        if variable is None or NotATemplate.is_tagged_on(variable):
            return variable  # input was not manipulated, assume that it contains only allowed types

        # FIXME: early exit on empty collections

        # FIXME: tighten this up, and figure out a better way to avoid propagating options
        if template_ctx := TemplateContext.current():
            # FIXME: ideally avoid re-creating TemplateOptions every time here
            options = options or TemplateOptions()  # FIXME: this is dangerous because it looks like it's the default, but it's a context-aware factory method
            stop_on_template = template_ctx.stop_on_template
        else:
            options = options or _DEFAULT_TEMPLATE_OPTIONS
            stop_on_template = False

        if mode == TemplateMode.STOP_ON_TEMPLATE:
            stop_on_template = True

        # track access to items that are tagged Deprecated during templating, handle accordingly
        with (
            UndecryptableAccessMutator(),  # trigger injection of VaultBomb
            DeprecatedAccessAuditContext() as deprecated,
            # stack the current active var value we're templating
            TemplateContext(template_value=variable, templar=self, options=options, stop_on_template=stop_on_template) as template_ctx,
        ):
            try:
                if not isinstance(variable, str):
                    if options.overrides is not _TEMPLATE_OVERRIDE_DEFAULT:
                        raise ValueError("Jinja overrides are only allowed on string inputs")

                    template_result = _AnsibleLazyTemplateMixin.try_create(variable)
                elif mode is not TemplateMode.EXPRESSION and not is_possibly_template(variable, options.overrides):
                    template_result = variable
                elif not self._trust_check(variable, mode):
                    template_result = variable
                else:
                    compiled_template: t.Callable[[dict[str, t.Any] | ChainMap[str, t.Any]], t.Any]

                    if mode is TemplateMode.EXPRESSION:
                        compiled_template = self._compile_expression(variable, options)
                    elif stop_on_template:
                        raise TemplateEncountered()
                    else:
                        compiled_template = self._compile_template(variable, options)

                    template_result = compiled_template(self.available_variables)
                    template_result = self._post_render_mutation(variable, template_result, options)

                # If we're the outermost template operation, we need to recursively finalize the template result.
                # This will render any embedded templates and trigger undefined, omit and vault bomb behaviors.
                if template_ctx.is_top_level:
                    if mode is TemplateMode.STOP_ON_CONTAINER and type(template_result) in AnsibleTaggedObject._collection_types:
                        # Use of STOP_ON_CONTAINER implies the caller will perform necessary checks on values,
                        # most likely by passing them back into the templating system.
                        try:
                            return template_result.untemplated_tagged_copy()
                        except AttributeError:
                            return template_result  # FIXME: how can we get here?

                    # data is our only positional arg, everything else is kwargs-only
                    with DetonateVaultBombsTripwire():
                        template_result = _finalize_template_result(template_result, mode=FinalizeMode.TOP_LEVEL)
                        template_result = options.undefined_behavior.post_finalize(template_result)

                    # Check for Omit as a template result after post_finalize, which may have converted an AnsibleUndefined to Omit.
                    # When value_for_omit is Omit and the template result is Omit, raise an AnsibleValueOmittedError.
                    # Other occurrences of Omit will simply drop out of containers during _finalize_template_result or post_finalize.
                    if template_result is Omit:
                        if options.value_for_omit is Omit:
                            raise AnsibleValueOmittedError()

                        return options.value_for_omit  # value_for_omit was not manipulated, trust that it contains only allowed types
            except TemplateEncountered:
                raise
            except Exception as ex:
                self._raise_template_error(ex, variable, mode)

        self._emit_deprecation_warnings(deprecated)

        return template_result

    def _compile_template(self, template: str, options: TemplateOptions) -> AnsibleTemplate:
        # NOTE: Creating an overlay that lives only inside _compile_template means that overrides are not applied
        # when templating nested variables, where Templar.environment is used, not the overlay. They are, however,
        # applied to includes and imports.
        stripped_template, env = self._create_overlay(template, options.overrides)

        with _TemplateCompileContext(escape_backslashes=options.escape_backslashes):
            compiled_template = t.cast(AnsibleTemplate, env.from_string(stripped_template))

        return compiled_template

    def _compile_expression(self, expression: str, options: TemplateOptions) -> AnsibleTemplateExpression:
        """
        Compile a Jinja expression, applying optional compile-time behavior via an environment overlay (if needed). The overlay is
        necessary to avoid mutating settings on the Templar's shared environment, which could be visible to other code running concurrently.
        In the specific case of escape_backslashes, the setting only applies to a top-level template at compile-time, not runtime, to
        ensure that any nested template calls (e.g., include and import) do not inherit the (lack of) escaping behavior.
        """
        with _TemplateCompileContext(escape_backslashes=options.escape_backslashes):
            return AnsibleTemplateExpression(self.environment.compile_expression(expression, False))

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
                newlines = options.overrides.newline_sequence * (data_newlines - res_newlines)
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

    @staticmethod
    def _emit_deprecation_warnings(deprecated: DeprecatedAccessAuditContext) -> None:
        # FIXME: create a dataclass or something for runtime capture of deprecation info plus the template context the access occurred in
        for deprecation_template, deprecation in deprecated.deprecated_access:
            # FIXME: if we're in a worker, propagate deprecated access warnings back to the controller for deduplication
            # FIXME: the current template may not have a source position, we may need to consult a parent template
            _display.deprecated(
                msg=f'{deprecation.msg} while templating {_repr_from(deprecation_template)}',
                version=deprecation.removal_version,
                date=deprecation.removal_date,
            )

    @staticmethod
    def _raise_template_error(ex: Exception, variable: t.Any, mode: TemplateMode) -> t.NoReturn:
        # FIXME: capture useful context information from each context early

        if isinstance(ex, AnsibleTemplateError):
            exception_to_raise = ex
        else:
            cause = 'expression' if mode is TemplateMode.EXPRESSION else 'template'
            src_pos = AnsibleSourcePosition.get_tag(variable)

            if isinstance(variable, str):
                cause += f' {variable!r}'
            else:
                cause += f' of type {type(variable)}'

            if src_pos:
                cause += f' from {src_pos}'

            ex_type = AnsibleTemplateError  # always raise an AnsibleTemplateError/subclass
            if isinstance(ex, RecursionError):
                msg = f"Recursive loop detected in {cause}"
            elif isinstance(ex, TemplateSyntaxError):
                msg = f"Syntax error in {cause}: {ex}"
                ex_type = AnsibleTemplateSyntaxError
            else:
                msg = f"Unexpected exception rendering {cause}: {ex}"

            exception_to_raise = ex_type(NotATemplate().tag(msg), orig_exc=ex)

        # FIXME: apply captured context information from above onto `exception_to_raise` here, before (re)raising

        if exception_to_raise is ex:
            raise  # pylint: disable=misplaced-bare-raise

        raise exception_to_raise from ex

    def is_template(self, data) -> bool:
        try:
            self.template(data, mode=TemplateMode.STOP_ON_TEMPLATE)
        except TemplateEncountered:
            return True
        else:
            return False

    def _query_lookup(self, name, /, *args, **kwargs):
        """wrapper for lookup, force wantlist true"""
        kwargs['wantlist'] = True
        return self._lookup(name, *args, **kwargs)

    def _lookup(self, name, /, *args, **kwargs):
        # FIXME: we should probably be running the result of lookup plugins through proxy_or_render_template

        instance = lookup_loader.get(name, loader=self._loader, templar=self)

        if instance is None:
            raise AnsibleTemplatePluginNotFoundError(f"lookup plugin {name!r} not found")

        # some plugins make a poor assumption that `run` takes a list
        args = list(args)

        wantlist = kwargs.pop('wantlist', False)
        errors = kwargs.pop('errors', 'strict')

        # safely catch run failures per #5059
        try:
            ran = instance.run(args, variables=self.available_variables, **kwargs)
        # FIXME: most of this exception handling should occur at the edge of templating
        except AnsibleUndefinedVariable:
            # this is just to prevent the broad `except Exception` from firing below
            raise
        except UndefinedError:
            # this is just to prevent the broad `except Exception` from firing below
            raise
        except AnsibleOptionsError:
            # invalid options given to lookup, just reraise
            raise
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
                    raise AnsibleError("The lookup plugin '%s' did not return a list."
                                       % name)
        return ran

    def evaluate_expression(self, expression: str, escape_backslashes=True) -> t.Any:
        if not isinstance(expression, str):
            raise TypeError(f"evaluate_expression requires {str!r}, got {type(expression)!r}")

        return self.template(
            expression,
            options=TemplateOptions(escape_backslashes=escape_backslashes),
            mode=TemplateMode.EXPRESSION,
        )

    _BROKEN_CONDITIONAL_ALLOWED_FRAGMENT = ' Broken conditionals are currently allowed because the `ALLOW_BROKEN_CONDITIONALS` configuration option is enabled.'
    _BROKEN_CONDITIONAL_DISALLOWED_FRAGMENT = ' Broken conditionals can be temporarily allowed with the `ALLOW_BROKEN_CONDITIONALS` configuration option.'

    def evaluate_conditional(self, conditional: str | bool | None) -> bool:
        if type(conditional) is bool:  # pylint: disable=unidiomatic-typecheck
            return conditional

        if is_str := isinstance(conditional, str):
            # Always strip conditional input strings. Neither conditional expressions nor all-template conditionals have legit reasons to preserve
            # surrounding whitespace, and they complicate detection and processing of all-template fallback cases.
            conditional = conditional.strip()

        if conditional in (None, ''):
            # deprecated backward-compatible behavior; None/empty input conditionals are always True
            if not self._allow_broken_conditionals:
                raise AnsibleBrokenConditionalError("Empty conditional expressions are not allowed." + self._BROKEN_CONDITIONAL_DISALLOWED_FRAGMENT)

            _display.deprecated(msg='Empty conditional expression was evaluated as True.' + self._BROKEN_CONDITIONAL_ALLOWED_FRAGMENT, version='2.21')
            return True

        is_expression = is_str and not is_possibly_all_template(conditional, _TEMPLATE_OVERRIDE_DEFAULT)

        if is_str and not is_expression:
            _display.deprecated(
                msg=f'Conditional {_repr_from(conditional)} should not be surrounded by templating delimiters such as {{{{ }}}} or {{% %}}.',
                version='2.21',
            )

        try:
            if is_expression:
                with _RenderJinjaConstAsTemplate():
                    # Disable escape_backslashes when processing conditionals, to maintain backwards compatibility.
                    # This is necessary because conditionals were previously evaluated using {% %}, which was *NOT* affected by escape_backslashes.
                    # Now that conditionals use expressions, they would be affected by escape_backslashes if it was not disabled.
                    result = self.evaluate_expression(conditional, escape_backslashes=False)
            else:
                result = self.template(conditional)
        except AnsibleUndefinedVariable as ex:
            # FIXME: this feels wrong, but we've got so many places that are inconsistently handling/swallowing this error that
            #  at least the warning allows us a place to consistently present useful forensic information about the problem

            conditional_repr = _repr_from(conditional)

            _display.warning(f'Conditional {conditional_repr} evaluation failed: {ex}')

            raise AnsibleUndefinedVariable(f"error while evaluating conditional {conditional_repr}: {ex}") from ex

        if isinstance(result, bool):
            _display.debug(f"Evaluated conditional {conditional!r} : {result}")
            return result

        bool_result = bool(result)
        # FIXME: `type(result)` should probably be the base type of the data structure
        message = (
            f'Conditional {_repr_from(conditional)} had result {result!r} of type {type(result)}, which evaluates to {bool_result}. '
            f'Conditionals must have a boolean result.'
        )

        if self._allow_broken_conditionals:
            message += self._BROKEN_CONDITIONAL_ALLOWED_FRAGMENT

            _display.deprecated(msg=message, version='2.21')

            return bool_result

        message += self._BROKEN_CONDITIONAL_DISALLOWED_FRAGMENT

        raise AnsibleBrokenConditionalError(message)

    @staticmethod
    def _trust_check(data: str, mode: TemplateMode) -> bool:
        """
        Return True if the given template data is trusted for templating, otherwise return False.

        Emits a warning if the data is not trusted.
        """
        if not TrustedAsTemplate.is_tagged_on(data):
            if Templar._raise_on_trust_check_fail or mode is TemplateMode.EXPRESSION:
                thing = "expression" if mode is TemplateMode.EXPRESSION else "template"
                raise TemplateTrustCheckFailedError(f'Failing on untrusted {thing} {_repr_from(data)}. '
                                                    f'Expressions and templates must be defined by trusted sources such as playbooks, roles, etc., '
                                                    'and not untrusted sources such as module results.')

            # FIXME: make traceback optional
            tb = "\n".join(format_stack())
            _display.warning(f'skipped untrusted template {_repr_from(data)}; execution stack:\n{tb}')

            return False

        return True

    def proxy_or_render_template(self, item: t.Any, key: str | None = None):
        # FIXME: always blindly access item here?
        # FIXME: should we do something with key here, or remove it?
        return self.template(AnsibleAccessContext.current().access(item))

    def proxy_or_render_kwargs(self, kwargs: c.Mapping[str, t.Any]) -> dict[str, t.Any]:
        return {kwarg: self.proxy_or_render_template(value) for kwarg, value in kwargs.items()}
