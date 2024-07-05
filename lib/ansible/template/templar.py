# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import dataclasses
import enum
import os
import textwrap
import typing as t

from contextlib import contextmanager
from collections import ChainMap

from jinja2.exceptions import TemplateSyntaxError
from jinja2.loaders import FileSystemLoader

from ansible.errors import (
    AnsibleError,
    AnsibleValueOmittedError,
    AnsibleUndefinedVariable,
    AnsibleTemplateError,
    AnsibleTemplateSyntaxError,
    AnsibleBrokenConditionalError,
)
from ansible.module_utils.datatag import (
    AnsibleTaggedObject, NotTaggableError, AnsibleTagHelper
)
from ..utils.datatag.tags import AnsibleSourcePosition, TrustedAsTemplate, NotATemplate
from ansible.module_utils.datatag.access import AnsibleAccessContext

from ansible.utils.display import Display
from ansible.utils.vars import isidentifier
from ansible.parsing.dataloader import DataLoader

from .datatag import DeprecatedAccessAuditContext, _RenderJinjaConstAsTemplate
from .jinja_bits import AnsibleEnvironment, AnsibleTemplate, _TemplateCompileContext, TemplateOverrides, \
    is_possibly_template, is_possibly_all_template, AnsibleTemplateExpression, _finalize_template_result, FinalizeMode
from .jinja_common import AnsibleUndefined, _TemplateConfig
from .jinja_plugins import JinjaCallContext
from .vault import DetonateVaultBombsTripwire, UndecryptableAccessMutator
from .utils import Omit, TemplateContext
from .lazy_containers import _AnsibleLazyTemplateMixin
from .undefined_behaviors import FAIL_ON_UNDEFINED, UndefinedBehavior

_display = Display()


def as_non_templatable_text(value: t.Any) -> str:
    return AnsibleTagHelper.tag(str(value), AnsibleTagHelper.tags(value) | {NotATemplate()})


class TemplateTrustCheckFailedError(AnsibleTemplateError):
    pass


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TemplateOptions:
    DEFAULT: t.ClassVar[t.Self]

    preserve_trailing_newlines: bool = t.cast(bool, ...)
    escape_backslashes: bool = t.cast(bool, ...)
    overrides: TemplateOverrides = t.cast(TemplateOverrides, ...)  # DTFIX-MERGE: these aren't really overrides anymore, rename the dataclass and this field
    undefined_behavior: UndefinedBehavior = t.cast(UndefinedBehavior, ...)
    value_for_omit: t.Any = ...

    def __post_init__(self):
        if template_ctx := TemplateContext.current(optional=True):
            if self.value_for_omit is not ...:
                raise ValueError("value_for_omit is only valid for top-level template calls.")
            defaults = template_ctx.options
        else:
            try:
                defaults = TemplateOptions.DEFAULT
            except AttributeError:
                # HACK: stop post_init here when creating the shared defaults
                return

        # DTFIX-FUTURE: dataclasses.replace on the defaults in a factory?
        for field in dataclasses.fields(self):
            if getattr(self, field.name) is ...:
                # DTFIX-MERGE: figure out a better way to avoid propagating options
                # DTFIX-MERGE: review all options to determine correct propagation behavior
                if field.name in ('value_for_omit', 'overrides', 'escape_backslashes'):
                    value = getattr(TemplateOptions.DEFAULT, field.name)
                else:
                    value = getattr(defaults, field.name)

                object.__setattr__(self, field.name, value)


TemplateOptions.DEFAULT = TemplateOptions(
    preserve_trailing_newlines=True,
    escape_backslashes=True,
    overrides=TemplateOverrides.DEFAULT,
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

    _sentinel = object()

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
            self._environment = AnsibleEnvironment(
                extensions=_TemplateConfig.jinja_extensions,
                loader=FileSystemLoader(self.basedir),
            )

        return self._environment

    def _create_overlay(self, template: str, overrides: TemplateOverrides) -> tuple[str, AnsibleEnvironment]:
        try:
            template, overrides = overrides.extract_template_overrides(template)
        except Exception as ex:
            raise AnsibleTemplateSyntaxError("Syntax error in template.", obj=template) from ex

        env = self.environment

        if overrides is not TemplateOverrides.DEFAULT and (overlay_kwargs := overrides.overlay_kwargs()):
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
        # DTFIX-MERGE: ensure that we're always accessing this as a shallow container-level snapshot, and eliminate uses of set_temporary_context and anything
        #  else that directly mutates this value. _new_context may resolve this for us?
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

    def resolve_variable_expression(self, expression: str) -> t.Any:
        """Resolve a variable name or simple dotted variable expression."""
        components = expression.split('.')
        if not all(map(isidentifier, components)):
            # DTFIX-MERGE: better exception type here
            raise AnsibleError(f'invalid variable expression: {expression}', obj=expression)
        return self.evaluate_expression(TrustedAsTemplate().tag(expression))

    def template_literal_expression(self, expression: t.LiteralString, var_overrides: dict[str, t.Any] | None = None) -> t.Any:
        """Template string literal expressions with blind trust."""
        # DTFIX-RELEASE: implement a pylint check for proper usage of LiteralString args (even if mypy eventually supports it,
        #  we'll want this to get checked for collections, too).

        variables = ChainMap(var_overrides, self.available_variables) if var_overrides else self.available_variables
        templar = Templar(self._loader, variables=variables)
        return templar.evaluate_expression(TrustedAsTemplate().tag(expression))

    @staticmethod
    def variable_name_as_template(name: str) -> str:
        """Return a trusted template string that will resolve the provided variable name. Raises an error if `name` is not a valid identifier."""
        if not isidentifier(name):
            # DTFIX-MERGE: better exception type here
            raise AnsibleError(f"invalid variable name: {name!r}", obj=name)
        return AnsibleTagHelper.tag('{{' + name + '}}', (AnsibleTagHelper.tags(name) | {TrustedAsTemplate()}) - {NotATemplate()})

    def template(
            self,
            variable: t.Any,  # DTFIX-MERGE: once we settle the new/old API boundaries, rename this (here and in other methods)
            *,
            options: TemplateOptions | None = None,
            mode: TemplateMode = TemplateMode.DEFAULT,
            template_locals: dict[str, t.Any] | None = None,
    ) -> t.Any:
        """Templates (possibly recursively) any given data as input."""

        # bail out if we know we're looking at something that's been explicitly tagged as not a template
        if variable is None:
            return variable  # input was not manipulated, assume that it contains only allowed types

        if mode == TemplateMode.EXPRESSION:
            if not isinstance(variable, str):
                raise TypeError(f"Expressions must be {str!r}, got {type(variable)!r}.")
        elif NotATemplate.is_tagged_on(variable):
            # When processing a template (ie, not an expression), silently ignore strings that were explicitly tagged NotATemplate.
            # The exclusion of expression mode supports testing untrusted constants from playbooks tagged with `!unsafe` (since it applies NotATemplate).
            # Plugins could encounter this situation if evaluate_expression is called with a value tagged NotATemplate.
            return variable

        # DTFIX-FUTURE: early exit on empty collections (hot code path for playbook templating/validation)

        # DTFIX-MERGE: tighten this up, and figure out a better way to avoid propagating options
        if template_ctx := TemplateContext.current(optional=True):
            # DTFIX-FUTURE: ideally avoid re-creating TemplateOptions every time here
            options = options or TemplateOptions()  # DTFIX-MERGE: dangerous because it looks like it's the default, but it's a context-aware factory method
            stop_on_template = template_ctx.stop_on_template
        else:
            options = options or TemplateOptions.DEFAULT
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
                    if options.overrides is not TemplateOverrides.DEFAULT:
                        raise ValueError("Jinja overrides are only allowed on string inputs")

                    template_result = _AnsibleLazyTemplateMixin._try_create(variable)
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

                    template_variables: dict[str, t.Any] | ChainMap[str, t.Any]

                    if template_locals:
                        template_variables = ChainMap(template_locals, self.available_variables)
                    else:
                        template_variables = self.available_variables

                    template_result = compiled_template(template_variables)
                    template_result = self._post_render_mutation(variable, template_result, options)

                # If we're the outermost template operation, we need to recursively finalize the template result.
                # This will render any embedded templates and trigger undefined, omit and vault bomb behaviors.
                if template_ctx.is_top_level:
                    if template_result is Omit:
                        # When the template result is Omit, raise an AnsibleValueOmittedError if value_for_omit is Omit, otherwise return value_for_omit.
                        # Other occurrences of Omit will simply drop out of containers during _finalize_template_result.
                        if options.value_for_omit is Omit:
                            raise AnsibleValueOmittedError()

                        return options.value_for_omit  # trust that value_for_omit is an allowed type

                    if mode is TemplateMode.STOP_ON_CONTAINER and type(template_result) in AnsibleTaggedObject._collection_types:
                        # Use of STOP_ON_CONTAINER implies the caller will perform necessary checks on values,
                        # most likely by passing them back into the templating system.
                        try:
                            return template_result._non_lazy_copy()
                        except AttributeError:
                            return template_result  # non-lazy containers are returned as-is

                    with DetonateVaultBombsTripwire():
                        template_result = _finalize_template_result(template_result, mode=FinalizeMode.TOP_LEVEL)
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
                result = AnsibleTagHelper.tag_copy(result, result + newlines)

        # If the input string template was source-tagged and the result is not, propagate the source tag to the new value.
        # This provides further contextual information when a template-derived value/var causes an error.
        if (src_pos := AnsibleSourcePosition.get_tag(template)) and not AnsibleSourcePosition.is_tagged_on(result):
            try:
                result = src_pos.tag(result)
            except NotTaggableError:
                pass  # best effort- if we can't, oh well

        return result

    @staticmethod
    def _emit_deprecation_warnings(deprecated: DeprecatedAccessAuditContext) -> None:
        for item in deprecated.deprecated_access:
            if AnsibleSourcePosition.is_tagged_on(item.template):
                msg = item.deprecated.msg
            else:
                # without a source position, we need to include what context we do have (the template)
                msg = f'While processing {item.template!r}: {item.deprecated.msg}'

            _display.deprecated(
                msg=msg,
                version=item.deprecated.removal_version,
                date=item.deprecated.removal_date,
                obj=item.template,
            )

    @staticmethod
    def _raise_template_error(ex: Exception, variable: t.Any, mode: TemplateMode) -> t.NoReturn:
        # DTFIX-FUTURE: capture useful context information from each context on the way in, not the way out

        if isinstance(ex, AnsibleTemplateError):
            exception_to_raise = ex
        else:
            cause = 'expression' if mode is TemplateMode.EXPRESSION else 'template'

            if isinstance(variable, str):
                if len(variable) <= 60 and '\n' not in variable:  # DTFIX-FUTURE: implement better configuration on inline template elision/presentation
                    cause += f' {variable!r}'
            else:
                cause += f' of type {type(variable)}'  # augment errors for non-string inputs with type information

            ex_type = AnsibleTemplateError  # always raise an AnsibleTemplateError/subclass

            if isinstance(ex, RecursionError):
                msg = f"Recursive loop detected in {cause}."
            elif isinstance(ex, TemplateSyntaxError):
                msg = f"Syntax error in {cause}."
                ex_type = AnsibleTemplateSyntaxError
            else:
                msg = f"Error rendering {cause}."

            exception_to_raise = ex_type(NotATemplate().tag(msg), obj=variable)

        if not exception_to_raise.obj:
            exception_to_raise.obj = TemplateContext.current().template_value

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

    def evaluate_expression(self, expression: str, escape_backslashes=True, template_locals: dict[str, t.Any] | None = None) -> t.Any:
        # DTFIX-MERGE: allow passing TemplateOptions here, so the debug action can use this instead of template(mode=TemplateMode.EXPRESSION)

        return self.template(
            expression,
            options=TemplateOptions(escape_backslashes=escape_backslashes),
            mode=TemplateMode.EXPRESSION,
            template_locals=template_locals,
        )

    _BROKEN_CONDITIONAL_ALLOWED_FRAGMENT = 'Broken conditionals are currently allowed because the `ALLOW_BROKEN_CONDITIONALS` configuration option is enabled.'

    def evaluate_conditional(self, conditional: str | bool | None) -> bool:
        if type(conditional) is bool:  # pylint: disable=unidiomatic-typecheck
            return conditional

        if is_str := isinstance(conditional, str):
            # Always strip conditional input strings. Neither conditional expressions nor all-template conditionals have legit reasons to preserve
            # surrounding whitespace, and they complicate detection and processing of all-template fallback cases.
            conditional = conditional.strip()

        if conditional in (None, ''):
            # deprecated backward-compatible behavior; None/empty input conditionals are always True
            if _TemplateConfig.allow_broken_conditionals:
                _display.deprecated(
                    msg='Empty conditional expression was evaluated as True.',
                    help_text=self._BROKEN_CONDITIONAL_ALLOWED_FRAGMENT,
                    obj=conditional,
                    version='2.21',
                )
                return True

            raise AnsibleBrokenConditionalError("Empty conditional expressions are not allowed.", obj=conditional)

        is_expression = is_str and not is_possibly_all_template(conditional)

        if is_str and not is_expression:
            msg = 'Conditionals should not be surrounded by templating delimiters such as {{ }} or {% %}.'

            if _TemplateConfig.allow_embedded_templates:
                _display.deprecated(msg=msg, obj=conditional, version='2.21')
            else:
                raise AnsibleBrokenConditionalError(message=msg, obj=conditional)

        try:
            if not is_str:
                if _TemplateConfig.allow_broken_conditionals:
                    # because the input isn't a string, the result will never be a bool; the broken conditional warning below will apply on the result
                    result = self.template(conditional)
                else:
                    raise AnsibleBrokenConditionalError(
                        message="Conditional expressions must be strings.",
                        obj=conditional,
                    )

            elif _TemplateConfig.allow_embedded_templates:
                if is_expression:
                    with _RenderJinjaConstAsTemplate():
                        # Disable escape_backslashes when processing conditionals, to maintain backwards compatibility.
                        # This is necessary because conditionals were previously evaluated using {% %}, which was *NOT* affected by escape_backslashes.
                        # Now that conditionals use expressions, they would be affected by escape_backslashes if it was not disabled.
                        result = self.evaluate_expression(conditional, escape_backslashes=False)
                else:
                    result = self.template(conditional)
            else:
                result = self.evaluate_expression(conditional, escape_backslashes=False)
        except AnsibleUndefinedVariable as ex:
            # DTFIX-FUTURE: we're only augmenting the message for context here; once we have proper contextual tracking, we can dump the re-raise
            raise AnsibleUndefinedVariable("Error while evaluating conditional.", obj=conditional) from ex

        if isinstance(result, bool):
            return result

        bool_result = bool(result)

        msg = (
            f'Conditional result was {textwrap.shorten(str(result), width=40)!r} of type {AnsibleTagHelper.get_friendly_type_name(result)!r}, '
            f'which evaluates to {bool_result}. Conditionals must have a boolean result.'
        )

        if _TemplateConfig.allow_broken_conditionals:
            _display.deprecated(msg=msg, obj=conditional, help_text=self._BROKEN_CONDITIONAL_ALLOWED_FRAGMENT, version='2.21')

            return bool_result

        raise AnsibleBrokenConditionalError(msg, obj=conditional)

    @staticmethod
    def _trust_check(data: str, mode: TemplateMode) -> bool:
        """
        Return True if the given template data is trusted for templating, otherwise return False.

        Emits a warning if the data is not trusted.
        """
        if not TrustedAsTemplate.is_tagged_on(data):
            help_text = ('Expressions and templates must be defined by trusted sources such as playbooks, roles, etc., '
                         'and not untrusted sources such as module results.')

            if _TemplateConfig.raise_on_trust_check_fail or mode is TemplateMode.EXPRESSION:
                thing = "expression" if mode is TemplateMode.EXPRESSION else "template"

                raise TemplateTrustCheckFailedError(
                    message=f'Failing on untrusted {thing}.',
                    help_text=help_text,
                    obj=data,
                )

            # DTFIX-FUTURE: Once we have defined what methods are entry-points into templating, we can use inspect.stack() to find the most recent non-template
            #   caller and use that for source position when no source position is available. This could be useful for situations where the template
            #   was embedded in a plugin, or a plugin is otherwise responsible for losing the source position and/or trust. We can't just use the first
            #   non-template caller as that will lead to false positivies for re-entrant calls (e.g. template plugins that call into templar).

            _display.warning('Skipped untrusted template.', help_text=help_text, obj=data)

            return False

        return True

    def proxy_or_render_template(self, item: t.Any):
        # DTFIX-MERGE: always blindly access item here?
        res = self.template(AnsibleAccessContext.current().access(item))

        if (type(res) is AnsibleUndefined and  # pylint: disable=unidiomatic-typecheck
                ((jc := JinjaCallContext.current(optional=True)) and jc.eager_trip_undefined)):
            res.trip()

        return res
