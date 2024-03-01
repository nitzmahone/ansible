from __future__ import annotations

import ast
import collections.abc as c
import dataclasses
import datetime
import enum
import functools
import os
import tempfile

from collections import ChainMap
from contextlib import nullcontext

from jinja2 import pass_context, defaults, UndefinedError, TemplateRuntimeError
from jinja2.environment import Environment, Template, TemplateModule, TemplateExpression
from jinja2.runtime import Undefined, StrictUndefined
from jinja2.compiler import Frame
from jinja2.lexer import TOKEN_VARIABLE_BEGIN, TOKEN_VARIABLE_END, TOKEN_STRING, Lexer
from jinja2.nativetypes import NativeCodeGenerator
from jinja2.nodes import Const
from jinja2.runtime import Context
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.utils import missing, LRUCache

from ansible.utils.display import Display
from ansible.errors import AnsibleError, AnsibleTemplatePluginNotFoundError, AnsibleVariableTypeError, AnsibleTemplatePluginError
from ansible.module_utils.common.text.converters import to_text, to_native
from ansible.module_utils.compat import typing as t
from ansible.module_utils.datatag import (
    TrustedAsTemplate,
    AnsibleSourcePosition,
    AnsibleTaggedObject,
    AnsibleDatatagBase,
    _AnsibleTaggedDict,
    _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES,
    _AnsibleTaggedList,
    _AnsibleTaggedTuple,
    _AnsibleTaggedSet,
    _inject_post_init_validation,
    Tripwire,
)
from ansible.module_utils.datatag.access import AnsibleAccessContext, AmbientContextBase
from ansible.module_utils.six import string_types
from ansible.module_utils import datatag
from ansible.plugins.loader import filter_loader, test_loader, Jinja2Loader
from .datatag import _JinjaConstTemplate

from .utils import Omit, TemplateContext, _repr_from
from .lazy_containers import (
    _AnsibleLazyTemplateMixin,
    _AnsibleLazyTemplateDict,
    _AnsibleLazyTemplateList,
    _AnsibleLazyTemplateTuple,
    _AnsibleLazyTemplateSet,
    _AnsibleLazyListAdapter, _AnsibleRangeListAdapter,
)
from .vault import _AnsibleTaggedVaultBomb

JINJA2_OVERRIDE = '#jinja2:'

display = Display()


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TemplateOverrides:
    block_start_string: str = defaults.BLOCK_START_STRING
    block_end_string: str = defaults.BLOCK_END_STRING
    variable_start_string: str = defaults.VARIABLE_START_STRING
    variable_end_string: str = defaults.VARIABLE_END_STRING
    comment_start_string: str = defaults.COMMENT_START_STRING
    comment_end_string: str = defaults.COMMENT_END_STRING
    line_statement_prefix: str | None = defaults.LINE_STATEMENT_PREFIX
    line_comment_prefix: str | None = defaults.LINE_COMMENT_PREFIX
    trim_blocks: bool = True  # AnsibleEnvironment overrides this default, so don't use the Jinja default here
    lstrip_blocks: bool = defaults.LSTRIP_BLOCKS
    newline_sequence: t.Literal['\n', '\r\n', '\r'] = defaults.NEWLINE_SEQUENCE
    keep_trailing_newline: bool = defaults.KEEP_TRAILING_NEWLINE

    def __post_init__(self) -> None:
        pass  # overridden by _inject_post_init_validation

    def _post_validate(self) -> None:
        if not (self.block_start_string != self.variable_start_string != self.comment_start_string != self.block_start_string):
            raise ValueError('Block, variable and comment start strings must be different.')

    def overlay_kwargs(self) -> dict[str, t.Any]:
        """
        Return a dictionary of arguments for passing to Environment.overlay.
        The dictionary will be empty if all fields have their default value.
        """
        # FIXME: calculate default/non-default during __post_init__
        fields = [(field, getattr(self, field.name)) for field in dataclasses.fields(self)]
        kwargs = {field.name: value for field, value in fields if value != field.default}

        return kwargs

    def contains_start_string(self, value: str) -> bool:
        """Returns True if the given value contains a variable, block or comment start string."""
        # FIXME: this is inefficient, use a compiled regex instead
        #        when fixing this, rename this function and include the line statement and line comment prefixes too (even though we don't yet need them)

        for marker in (self.block_start_string, self.variable_start_string, self.comment_start_string):
            if marker in value:
                return True

        return False

    def starts_and_ends_with_jinja_delimiters(self, value: str) -> bool:
        """Returns True if the given value starts and ends with Jinja variable, block or comment delimiters."""
        # FIXME: this is inefficient, use a compiled regex instead
        #        when fixing this, rename this function and include the line statement and line comment prefixes too (even though we don't yet need them)

        for marker in (self.block_start_string, self.variable_start_string, self.comment_start_string):
            if value.startswith(marker):
                break
        else:
            return False

        for marker in (self.block_end_string, self.variable_end_string, self.comment_end_string):
            if value.endswith(marker):
                return True

        return False

    def extract_template_overrides(self, template: str) -> tuple[str, TemplateOverrides]:
        if template.startswith(JINJA2_OVERRIDE):
            eol = template.find('\n')

            if eol == -1:
                raise ValueError(f"Missing newline after Jinja2 override: {_repr_from(template)}")

            line = template[len(JINJA2_OVERRIDE):eol]
            template = template[eol + 1:]
            override_kwargs = {}

            for pair in line.split(','):
                if ':' not in pair:
                    raise ValueError(f"Failed to parse Jinja2 override {pair!r}. Did you use something different from colon as key-value separator?")

                key, val = pair.split(':', 1)
                key = key.strip()

                if key not in _TEMPLATE_OVERRIDE_FIELD_NAMES:
                    raise ValueError(f"Invalid Jinja2 environment override key: {key!r}.")

                override_kwargs[key] = ast.literal_eval(val)

            overrides = dataclasses.replace(self, **override_kwargs)
        else:
            overrides = self

        return template, overrides


_inject_post_init_validation(TemplateOverrides, allow_subclasses=True)

_TEMPLATE_OVERRIDE_DEFAULT: t.Final[TemplateOverrides] = TemplateOverrides()
_TEMPLATE_OVERRIDE_FIELD_NAMES: t.Final[tuple[str, ...]] = tuple(sorted(field.name for field in dataclasses.fields(TemplateOverrides)))


class AnsibleContext(Context):
    """
    A custom context which intercepts resolve_or_missing() calls and
    runs them through AnsibleAccessContext. This allows usage of variables
    to be tracked. If needed, values can also be modified before being returned.
    """
    def __init__(self, *args, **kwargs):
        super(AnsibleContext, self).__init__(*args, **kwargs)

    __repr__ = object.__repr__  # prevent Jinja from dumping vars in case this gets repr'd

    def resolve_or_missing(self, key):
        val = super(AnsibleContext, self).resolve_or_missing(key)
        return AnsibleAccessContext.current().access(val)

    def get_all(self):
        # FIXME: explanatory docstring

        if not self.vars:
            return self.parent
        if not self.parent:
            return self.vars

        return ChainMap(self.vars, self.parent)

    # noinspection PyShadowingBuiltins
    def derived(self, locals: t.Optional[t.Dict[str, t.Any]] = None) -> Context:
        # this is a clone of Jinja's impl of derived, but using our lazy-aware _new_context

        context = _new_context(
            environment=self.environment,
            template_name=self.name,
            blocks={},
            shared=True,
            jinja_locals=locals,
            jinja_vars=self.get_all(),
        )
        context.eval_ctx = self.eval_ctx
        context.blocks.update((k, list(v)) for k, v in self.blocks.items())
        return context


class AnsibleTemplateExpression:
    """
    Wrapper around Jinja's TemplateExpression for converting AnsibleUndefinedError back into AnsibleUndefined.
    This is needed to make expression error handling consistent with templates, since Jinja does not support a custom type for Environment.compile_expression.
    """
    def __init__(self, template_expression: TemplateExpression) -> None:
        self._template_expression = template_expression

    def __call__(self, *args, **kwargs) -> t.Any:
        try:
            return self._template_expression(*args, **kwargs)
        except AnsibleUndefinedError as ex:
            return ex.source


class AnsibleTemplate(Template):
    """
    A helper class, which prevents Jinja2 from running lazy containers through dict().
    """

    _source_tempfile = None

    # FIXME: this still isn't working reliably; something else must be keeping the template object alive
    def __del__(self):
        if self._source_tempfile:
            os.unlink(self._source_tempfile.name)

    def __call__(self, *args, **kwargs) -> t.Any:
        return self.render(*args, **kwargs)

    # noinspection PyShadowingBuiltins
    def new_context(
        self,
        vars: c.Mapping[str, t.Any] | None = None,
        shared: bool = False,
        locals: c.Mapping[str, t.Any] | None = None,
    ) -> Context:
        return _new_context(
            environment=self.environment,
            template_name=self.name,
            blocks=self.blocks,
            shared=shared,
            jinja_locals=locals,
            jinja_vars=vars,
            jinja_globals=self.globals,
        )


# FIXME: give this a name that reflects its usage as an internal-only flow control exception
class AnsibleUndefinedError(UndefinedError):
    """
    An Ansible specific subclass of Jinja's UndefinedError, used to preserve and later restore the original AnsibleUndefined value that raised the error.
    This error is only raised by AnsibleUndefined and should never escape the templating system.
    """
    def __init__(self, message: str, source: AnsibleUndefined):
        super().__init__(message)

        self.source = source


class AnsibleUndefined(StrictUndefined, Tripwire):
    """A custom Undefined class, which returns further Undefined objects on access, rather than throwing an exception."""

    __slots__ = ('_undefined_template_source',)

    def __init__(
        self,
        hint: t.Optional[str] = None,
        obj: t.Any = missing,
        name: t.Optional[str] = None,
        exc: t.Type[TemplateRuntimeError] = UndefinedError,
        *args,
        _no_template_source=False,
        **kwargs,
    ) -> None:
        if not hint and name and obj is not missing:
            obj_type_name = (obj.native_type if isinstance(obj, AnsibleTaggedObject) else type(obj)).__name__
            hint = f"object of type {obj_type_name!r} has no attribute {name!r}"

        kwargs.update(
            hint=hint,
            obj=obj,
            name=name,
            exc=exc,
        )

        super().__init__(*args, **kwargs)

        if _no_template_source:
            self._undefined_template_source = None
        else:
            self._undefined_template_source = TemplateContext.current_or_raise().template_value

    # FIXME: we should probably intercept the dunder methods calling this instead -- and then make sure this function complains loudly if it is called
    def _fail_with_undefined_error(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise AnsibleUndefinedError(self._undefined_message, self)

    def trip(self) -> t.NoReturn:
        self._fail_with_undefined_error()

    def __getattr__(self, name):
        if name[:2] == "__":
            raise AttributeError(name)

        return self

    def __getitem__(self, key):
        return self

    # FIXME: do this right, have thorough tests to catch anything that slips through
    __repr__ = _fail_with_undefined_error
    __iter__ = __str__ = __len__ = _fail_with_undefined_error
    __eq__ = __ne__ = __bool__ = __hash__ = _fail_with_undefined_error
    __contains__ = _fail_with_undefined_error
    __add__ = __radd__ = __sub__ = __rsub__ = _fail_with_undefined_error
    __mul__ = __rmul__ = __div__ = __rdiv__ = _fail_with_undefined_error
    __truediv__ = __rtruediv__ = _fail_with_undefined_error
    __floordiv__ = __rfloordiv__ = _fail_with_undefined_error
    __mod__ = __rmod__ = _fail_with_undefined_error
    __pos__ = __neg__ = _fail_with_undefined_error
    __call__ = _fail_with_undefined_error
    __lt__ = __le__ = __gt__ = __ge__ = _fail_with_undefined_error
    __int__ = __float__ = __complex__ = _fail_with_undefined_error
    __pow__ = __rpow__ = _fail_with_undefined_error


# FIXME: decide if these should be taggable; do we need to support other kinds of Undefineds, etc
datatag._untaggable_types |= {AnsibleUndefined}


# FIXME: this is no longer used (previously part of J2Vars init to filter locals), should we still do this? Probably not...
def _process_locals(_l):
    if _l is None:
        return {}
    return {
        k: v for k, v in _l.items()
        if v is not missing
        and k not in {'context', 'environment', 'template'}  # NOTE is this really needed?
    }


class AnsibleCodeGenerator(NativeCodeGenerator):
    """
    Custom code generation behavior to support deprecated Ansible features and fill in gaps in Jinja native.
    This can be removed once the deprecated Ansible features are removed and the native fixes are upstreamed in Jinja.
    """

    def _output_const_repr(self, group: t.Iterable[t.Any]) -> str:
        """
        Prevent Jinja's code generation from stringifying single nodes before generating its repr.
        This complements the behavioral change in AnsibleEnvironment.concat which returns single nodes without stringifying them.
        """
        # TODO: contribute this upstream as a fix to Jinja's native support
        group_list = list(group)

        if len(group_list) == 1:
            return repr(group_list[0])

        # NB: This is slightly more efficient than Jinja's _output_const_repr, which generates a throw-away list instance to pass to join.
        #     Before removing this, ensure that upstream Jinja has this change.
        return repr("".join(map(str, group_list)))

    def visit_Const(self, node: Const, frame: Frame) -> None:
        """
        Override Jinja's visit_Const to inject a runtime call to AnsibleEnvironment._access_const for constant strings that are possibly templates, which
        may require special handling at runtime. See that method for details. An example that hits this path:
        {{ lookup("file", "{{ output_dir }}/bla") }}
        """
        value = node.as_const(frame.eval_ctx)

        if type(value) is str and is_possibly_template(value, _TEMPLATE_OVERRIDE_DEFAULT):  # pylint: disable=unidiomatic-typecheck
            # deprecated: description='embedded Jinja constant string template support' core_version='2.21'
            self.write(f'environment._access_const({value!r})')
        else:
            # NB: This is actually more efficient than Jinja's visit_Const, which contains obsolete (as of Py2.7/3.1) float conversion instance checks. Before
            #     removing this override entirely, ensure that upstream Jinja has removed the obsolete code.
            #     See https://docs.python.org/release/2.7/whatsnew/2.7.html#python-3-1-features for more details.
            self.write(repr(value))


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
        if not isinstance(key, string_types):
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
                display.vvvv('Unexpected plugin load (%s) exception: %s' % (key, to_native(e)))
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

            # FIXME: we should probably be running the result of filter plugins through proxy_or_render_template
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
            # FIXME: see question in __call__ about needing to wrap input args
            tc = TemplateContext.current_or_raise()
            templar = tc.templar
            args = templar.proxy_or_render_template(args)
            kwargs = templar.proxy_or_render_kwargs(kwargs)

            try:
                test_res = func(*args, **kwargs)
            except AnsibleUndefinedError:
                raise
            except Exception as ex:
                raise AnsibleTemplatePluginError(f"Test {plugin_name!r} failed: {ex}") from ex

            if not isinstance(test_res, bool):
                template = tc.template_value
                display.deprecated(
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
            # FIXME: see question in __call__ about needing to wrap input args
            templar = TemplateContext.current_or_raise().templar
            args = templar.proxy_or_render_template(args)
            kwargs = templar.proxy_or_render_kwargs(kwargs)

            try:
                filter_res = func(*args, **kwargs)
            except AnsibleUndefinedError:
                raise
            except Exception as ex:
                raise AnsibleTemplatePluginError(f"Filter {plugin_name!r} failed: {ex}") from ex

            return templar.proxy_or_render_template(filter_res)

        return wrapper


@pass_context
def _ansible_finalize(_ctx: AnsibleContext, value: t.Any) -> t.Any:
    """
    This function is called by Jinja with the result of each variable template block (e.g., {{ }}) encountered in a template.
    We're not using the passed in AnsibleContext or modifying the value.
    The pass_context decorator prevents finalize from being called on constants at template compile time.
    The important part for us is that this blocks constant folding, which ensures our custom visit_Const is used.
    """
    return value


@dataclasses.dataclass(kw_only=True, slots=True)
class _TemplateCompileContext(AmbientContextBase):
    escape_backslashes: bool


class _CompileStateSmugglingCtx(AmbientContextBase):
    template_source: str | None = None
    python_source: str | None = None
    filename: str | None = None
    tempfile: t.Any = None  # FIXME: what should this type hint be?


class AnsibleLexer(Lexer):
    """
    Lexer override to escape backslashes in string constants within Jinja expressions; prevents Jinja from double-escaping them.

    NOTE: This behavior is only applied to string constants within Jinja expressions (eg {{ "c:\newfile" }}), *not* statements ("{% set foo="c:\\newfile" %}").

    This is useful when templates are sourced from YAML double-quoted strings, as it avoids having backslashes processed twice: first by the
    YAML parser, and then again by the Jinja parser. Instead, backslashes are only processed by YAML.

    Example YAML:

    - debug:
        msg: "Test Case 1\\3; {{ test1_name | regex_replace('^(.*)_name$', '\\1')}}"

    Since the outermost YAML string is double-quoted, the YAML parser converts the double backslashes to single backslashes. Without escaping, Jinja
    would see only a single backslash ('\1') while processing the embedded template expression, interpret it as an escape sequence, and convert it
    to '\x01' (ASCII "SOH"). This is clearly not the intended `\1` backreference argument to the `regex_replace` filter (which would require the
    double-escaped string '\\\\1' to yield the intended result).

    Since the "\\3" in the input YAML was not part of a template expression, the YAML-parsed "\3" remains after Jinja rendering. This would be
    confusing for playbook authors, as different escaping rules would be needed inside and outside the template expression.

    When templates are not sourced from YAML, escaping backslashes will prevent use of backslash escape sequences such as "\n" and "\t".

    See relevant Jinja lexer impl at e.g.: https://github.com/pallets/jinja/blob/3.1.2/src/jinja2/lexer.py#L646-L653.
    """

    def tokeniter(self, *args, **kwargs) -> t.Iterator[t.Tuple[int, str, str]]:
        """Pre-escape backslashes in expression ({{ }}) raw string constants before Jinja's Lexer.wrap() can interpret them as ASCII escape sequences."""
        token_stream = super().tokeniter(*args, **kwargs)

        # if we have no context, Jinja's doing a nested compile at runtime (eg, import/include); historically, no backslash escaping is performed
        if not (tcc := _TemplateCompileContext.current()) or not tcc.escape_backslashes:
            yield from token_stream
            return

        in_variable = False

        for token in token_stream:
            token_type = token[1]

            if token_type == TOKEN_VARIABLE_BEGIN:
                in_variable = True
            elif token_type == TOKEN_VARIABLE_END:
                in_variable = False
            elif in_variable and token_type == TOKEN_STRING:
                token = token[0], token_type, token[2].replace('\\', '\\\\')

            yield token


class AnsibleEnvironment(ImmutableSandboxedEnvironment):
    """
    Our custom environment, which simply allows us to override the class-level
    values for the Template and Context classes used by jinja2 internally.
    """
    context_class = AnsibleContext
    template_class = AnsibleTemplate
    code_generator_class = AnsibleCodeGenerator
    intercepted_binops = frozenset({'eq', })
    _lexer_cache = LRUCache(50)

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

        self.globals.update(
            range=range,  # the sandboxed environment limits range in ways that may cause us problems; use the real Python one
            now=self._now,
            undef=_undef,
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

    @property
    def lexer(self):
        """Return/cache an AnsibleLexer with settings from the current AnsibleEnvironment"""
        # FIXME: we should pre-generate the default cached lexer before forking, not leave it to chance (e.g. simple playbooks)
        # FIXME: more efficient key calculation
        key = tuple(getattr(self, name) for name in _TEMPLATE_OVERRIDE_FIELD_NAMES)

        lex = self._lexer_cache.get(key)

        if lex is None:
            self._lexer_cache[key] = lex = AnsibleLexer(self)

        return lex

    _FIXME_DEBUGGABLE_TEMPLATE_SOURCE = False

    def from_string(self, *args, **kwargs):
        # FIXME: sane way to make this work outside from_string?
        compilectx = _CompileStateSmugglingCtx if self._FIXME_DEBUGGABLE_TEMPLATE_SOURCE else nullcontext

        with compilectx() as ctx:
            template_obj = super().from_string(*args, **kwargs)
            if compilectx is _CompileStateSmugglingCtx:
                template_obj._source_tempfile = ctx.tempfile

        return template_obj

    def _parse(self, source, *args, **kwargs):
        if csc := _CompileStateSmugglingCtx.current():
            csc.template_source = source
        return super()._parse(source, *args, **kwargs)

    def _compile(self, source, filename):
        if csc := _CompileStateSmugglingCtx.current():
            source = '\n'.join((
                "import sys; breakpoint() if type(sys.breakpointhook) is not type(breakpoint) else None",
                "# original template source (FIXME include source position): ",
                '\n'.join(f'# {line}' for line in (csc.template_source or '').splitlines()),
                source
            ))

            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', prefix='j2_src_', delete=False) as source_file:
                filename = source_file.name
                source_file.write(source)

            csc.python_source = source
            csc.filename = filename
            csc.tempfile = source_file
        res = super()._compile(source, filename)
        return res

    @staticmethod
    def concat(nodes: t.Iterable[t.Any]) -> t.Any:  # type: ignore[override]
        node_list = list(_flatten_nodes(nodes))

        if not node_list:
            return None

        # this code is complemented by our tweaked CodeGenerator _output_const_repr that ensures that literal constants
        # in templates aren't double-repr'd in the generated code
        if len(node_list) == 1:
            # FIXME: determine if we should do managed access here (we *should* have hit them all during templating/resolve, but ?)
            return node_list[0]

        # in order to ensure that all embedded triggers fire (vaultbomb, undefined, etc.), do a recursive finalize before we repr (otherwise we can end up
        # repr'ing Undefineds etc.) Yes, this requires two passes, but means we don't need to have a parallel reimplementation of all reprs
        try:
            node_list = _finalize_template_result(node_list, mode=FinalizeMode.CONCAT)
        except AnsibleUndefinedError as ex:
            return ex.source  # return the first AnsibleUndefined encountered (FailOnUndefined behavior)

        node_list = TemplateContext.current_or_raise().options.undefined_behavior.post_finalize(node_list)

        return ''.join([to_text(v) for v in node_list])

    @staticmethod
    def _access_const(const_template: t.LiteralString) -> t.Any:
        """
        Called during template rendering on template-looking string constants embedded in the template. Propagates source position from the
        containing template, and performs a managed access on it. This allows custom behavior on constants for backward-compatibility (eg,
        application of trust or inline template rendering).
        """
        # deprecated: description='embedded Jinja constant string template support' core_version='2.21'
        display.deprecated(msg=f"Jinja constant strings should not contain embedded templates: {_repr_from(const_template)}", version="2.21")

        tags: list[AnsibleDatatagBase] = [_JinjaConstTemplate()]

        if (tv := TemplateContext.current().template_value) and (source_pos := AnsibleSourcePosition.get_tag(tv)):
            tags.append(source_pos)

        return AnsibleAccessContext.current().access(AnsibleTaggedObject.tag(const_template, tags))

    @staticmethod
    def _render_const_template(const_template: t.LiteralString) -> t.Any:
        """
        This method is for exclusive use by the template compiler to render embedded constant templates.
        Since these values may be stored in locals that will receive no further processing before use, they must be trusted and templated, not just trusted.
        """
        # example: "{{ '{{ "hi" }}' }}" -- const_template is '{{ "hi" }}'
        # access on const_template should not be necessary
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(TrustedAsTemplate().tag(const_template))

    def getitem(self, obj: t.Any, argument: t.Any) -> t.Any:
        # FIXME: do we actually need to managed-access both sides of templates/strings here?
        # example: "{{ some['thing'] }}" -- obj is the "some" dict, argument is "thing"
        # access on the result of super().getitem is necessary
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getitem(obj, argument))

    def getattr(self, obj: t.Any, attribute: str) -> t.Any:
        # example: "{{ some.thing }}" -- obj is the "some" dict, argument is "thing"
        # access on the result of super().getattr is necessary
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getattr(obj, attribute))

    def call(
        self,
        __context: Context,
        __obj: t.Any,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> t.Any:
        templar = TemplateContext.current_or_raise().templar

        # FUTURE: this doesn't scale well, as we add more globals that need special handling, we may want to move that down into the globals
        if __obj == templar._lookup or __obj == templar._query_lookup:  # we can't use reference equality here; bound methods differ by instance
            lookup_name = args[0]
            args = templar.proxy_or_render_template(_trust_jinja_constants(args[1:]))  # for backwards compat, only trust constant templates in lookup pos args
            kwargs = templar.proxy_or_render_kwargs(kwargs)

            call_res = super().call(__context, __obj, lookup_name, *args, **kwargs)
        else:
            # FIXME: is there any case where proxy_or_render_template is actually needed for non-lookup inputs?
            #        variables from storage should already be lazy, constants aren't supported outside lookups, so what's left?
            #        one thing this does give us is access, is that why it's here? if so, document and add tests to cover it
            call_res = super().call(__context, __obj, *templar.proxy_or_render_template(args), **templar.proxy_or_render_kwargs(kwargs))

        return templar.proxy_or_render_template(call_res)

    @staticmethod
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
    Apply TrustedAsTemplate to values tagged with _JinjaConstTemplate and warn about not using templates in constants.
    Used to provide backwards compatiblity with historical lookup behavior for positional arguments.
    """
    # FIXME: needs tests to exercise this
    o_type = type(o)

    if _JinjaConstTemplate.is_tagged_on(o):
        return TrustedAsTemplate().tag(_JinjaConstTemplate.untag(o))

    if o_type is dict:
        return {k: _trust_jinja_constants(v) for k, v in o.items()}

    if o_type in (list, tuple, set):
        return o_type(_trust_jinja_constants(v) for v in o)

    return o


_DEFAULT_UNDEF = AnsibleUndefined("Mandatory variable has not been overridden", _no_template_source=True)


# FIXME: give this a proper name
def _undef(hint=None):
    """Jinja2 global function (undef) for creating custom undefined defaults with custom hints."""
    if hint is None or isinstance(hint, Undefined) or hint == '':
        return _DEFAULT_UNDEF

    return AnsibleUndefined(hint)


def _flatten_nodes(nodes: t.Iterable[t.Any]) -> t.Iterable[t.Any]:
    """
    Yield nodes from a potentially recursive iterable of nodes.
    The recursion is required to expand template imports (TemplateModule).
    Any UndefinedError exception encountered will be converted to an AnsibleUndefined instance.
    """
    iterator = iter(nodes)

    while True:
        try:
            node = next(iterator)
        except StopIteration:
            break
        except AnsibleUndefinedError as ex:
            # Convert an AnsibleUndefinedError generated internally by Jinja2 back into an AnsibleUndefined instance.
            # This instance may be embedded in a data structure and will be subject to UndefinedBehavior handling during template finalization.
            yield ex.source
            # Normal error handling will convert the first AnsibleUndefined encountered into an exception, ignoring any further AnsibleUndefined values.
            # When using ReplaceUndefined having a second AnsibleUndefined allows us to warn the user about potential omission of subsequent template nodes.
            # FUTURE: We should be able to accurately determine if truncation occurred by having the code generator smuggle out the number of expected nodes.
            yield AnsibleUndefined('template potentially truncated')
        else:
            if type(node) is TemplateModule:  # pylint: disable=unidiomatic-typecheck
                yield from _flatten_nodes(node._body_stream)
            else:
                yield node


def _flatten_and_lazify_vars(mapping: c.Mapping) -> t.Iterable[c.Mapping]:
    """Prevent deeply-nested Jinja vars ChainMaps from being created by nested contexts and ensure that all top-level containers support lazy templating."""
    mapping_type = type(mapping)
    if mapping_type is ChainMap:
        # noinspection PyUnresolvedReferences
        for m in mapping.maps:
            yield from _flatten_and_lazify_vars(m)
    elif mapping_type is _AnsibleLazyTemplateDict:
        yield mapping
    elif mapping_type in (dict, _AnsibleTaggedDict):
        yield _AnsibleLazyTemplateMixin.try_create(mapping)
    else:
        raise NotImplementedError(f"unsupported mapping type in Jinja vars: {mapping_type}")


def _new_context(
    *,
    environment: Environment,
    template_name: str | None,
    blocks: dict[str, t.Callable[[Context], c.Iterator[str]]],
    shared: bool = False,
    jinja_locals: c.Mapping[str, t.Any] | None = None,
    jinja_vars: c.Mapping[str, t.Any] | None = None,
    jinja_globals: c.MutableMapping[str, t.Any] | None = None,
) -> Context:
    """Override Jinja's context vars setup to use ChainMaps and containers that support lazy templating."""
    layers = []

    if jinja_locals:
        # FIXME: if we can't trip this in coverage, kill it off?
        if type(jinja_locals) is not dict:  # pylint: disable=unidiomatic-typecheck
            raise NotImplementedError("locals must be a dict")

        # Omit values set to Jinja's internal `missing` sentinel; they are locals that have not yet been
        # initialized in the current context, and should not be exposed to child contexts. e.g.: {% import 'a' as b with context %}.
        # The `b` local will be `missing` in the `a` context and should not be propagated as a local to the child context we're creating.
        layers.append(_AnsibleLazyTemplateMixin.try_create({k: v for k, v in jinja_locals.items() if v is not missing}))

    if jinja_vars:
        layers.extend(_flatten_and_lazify_vars(jinja_vars))

    if jinja_globals and not shared:
        # Even though we don't currently support templating globals, it's easier to ensure that everything is template-able rather than trying to
        # pick apart the ChainMaps to enforce non-template-able globals, or to risk things that *should* be template-able not being lazified.
        layers.extend(_flatten_and_lazify_vars(jinja_globals))

    # only return a ChainMap if we're combining layers, or we have none
    parent = layers[0] if len(layers) == 1 else ChainMap(*layers)

    # the `parent` cast is only to satisfy Jinja's overly-strict type hint
    return environment.context_class(environment, t.cast(dict, parent), template_name, blocks, globals=jinja_globals)


def is_possibly_template(value: str, overrides: TemplateOverrides):
    """
    A lightweight check to determine if the given string looks like it contains a template, even if that template is invalid.
    Return True if the given string starts with a Jinja overrides header or if it contains template start strings.
    """
    return value.startswith(JINJA2_OVERRIDE) or overrides.contains_start_string(value)


def is_possibly_all_template(value: str, overrides: TemplateOverrides):
    """
    A lightweight check to determine if the given string looks like it contains *only* a template, even if that template is invalid.
    Return True if the given string starts with a Jinja overrides header or if it starts and ends with Jinja template delimiters.
    """
    return value.startswith(JINJA2_OVERRIDE) or overrides.starts_and_ends_with_jinja_delimiters(value)


class FinalizeMode(enum.Enum):
    TOP_LEVEL = enum.auto()
    CONCAT = enum.auto()
    POST_FINALIZE = enum.auto()


# FIXME: add tests to ensure this doesn't drift from allowed types
def _finalize_template_result(o: t.Any, mode: FinalizeMode) -> t.Any:
    """
    Recurse the template result, rendering any encountered templates, converting containers to non-lazy versions.
    """
    o_type = type(o)

    from ansible.vars.hostvars import HostVars, HostVarsVars  # FIXME: really bad idea, don't do this -- this is here just to see if the tests pass otherwise

    value_type: type[dict | list | tuple | set]

    if o_type in _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES:
        return o
    # FIXME: delazifying HostVars/HostVarsVars here is correct but expensive- look at ways to do deferred lazy outside of templating or ?
    elif o_type in (dict, _AnsibleTaggedDict, _AnsibleLazyTemplateDict, HostVars, HostVarsVars):
        value_expression = ((
            _finalize_template_result(k, mode),
            _finalize_template_result(v, mode)
        ) for k, v in o.items() if v is not Omit)
        value_type = dict
    elif o_type in (list, _AnsibleTaggedList, _AnsibleLazyTemplateList, _AnsibleLazyListAdapter, _AnsibleRangeListAdapter):
        value_expression = (_finalize_template_result(v, mode) for v in o if v is not Omit)
        value_type = list
    elif o_type in (tuple, _AnsibleTaggedTuple, _AnsibleLazyTemplateTuple):
        value_expression = (_finalize_template_result(v, mode) for v in o if v is not Omit)
        value_type = tuple
    elif o_type in (set, _AnsibleTaggedSet, _AnsibleLazyTemplateSet):
        value_expression = (_finalize_template_result(v, mode) for v in o if v is not Omit)
        value_type = set
    elif o_type is AnsibleUndefined:
        # this early return assumes handle_undefined follows our variable type rules
        return TemplateContext.current_or_raise().options.undefined_behavior.handle_undefined(o, mode)
    elif o_type is _AnsibleTaggedVaultBomb:
        raise o.detonate()  # this raise is just to keep silly tools that don't understand NoReturn happy about value_type/expression not being assigned
    elif o is Omit:
        return o  # allow pass through of omit for later handling after top-level finalize completes
    elif mode is FinalizeMode.TOP_LEVEL:  # unsupported type (raise)
        raise AnsibleVariableTypeError(variable_type=o_type)
    else:  # unsupported type (do not raise)
        return o

    # avoiding tag_copy to minimize call stack depth when dealing with recursive template calls on deeply nested lazy containers
    return AnsibleTaggedObject.tag(value_expression, AnsibleTaggedObject.tags(o), value_type=value_type)
