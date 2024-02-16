from __future__ import annotations

import ast
import collections.abc as c
import dataclasses
import datetime
import functools
import os
import tempfile

from collections import ChainMap
from contextlib import nullcontext

from jinja2 import pass_context, defaults
from jinja2.environment import Environment, Template, TemplateModule
from jinja2.exceptions import UndefinedError
from jinja2.runtime import Undefined
from jinja2.compiler import Frame
from jinja2.lexer import TOKEN_VARIABLE_BEGIN, TOKEN_VARIABLE_END, TOKEN_STRING, Lexer
from jinja2.nativetypes import NativeCodeGenerator
from jinja2.nodes import Const
from jinja2.runtime import Context
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.utils import missing, LRUCache

from ansible.utils.display import Display
from ansible.errors import AnsibleError, AnsibleTemplatePluginNotFoundError
from ansible.module_utils.common.text.converters import to_text, to_native
from ansible.module_utils.compat import typing as t
from ansible.module_utils.datatag import TrustedAsTemplate, AnsibleSourcePosition, AnsibleTaggedObject, AnsibleDatatagBase
from ansible.module_utils.datatag.access import AnsibleAccessContext, AmbientContextBase
from ansible.module_utils.six import string_types
from ansible.plugins.loader import filter_loader, test_loader, Jinja2Loader
from .datatag import _JinjaConstTemplate, _JinjaConstToTrustedTemplate

from .utils import AnsibleUndefined, Omit, TemplateContext
from .lazy_containers import _finalize_template_result, _AnsibleLazyTemplateMixin, _AnsibleLazyTemplateDict, _AnsibleTaggedDict

RANGE_TYPE = type(range(0))

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
        types = t.get_type_hints(TemplateOverrides, globalns=globals())

        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            expected_type = types[field.name]

            if t.get_origin(expected_type) is t.Literal:
                if value not in t.get_args(expected_type):
                    raise ValueError(f'Template override {field.name!r} is {value!r} instead of one of {t.get_args(expected_type)!r}.')
            elif not isinstance(value, expected_type):
                raise TypeError(f'Template override {field.name!r} is {type(value)!r} instead of {expected_type!r}.')

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
                from .templar import Templar
                raise ValueError(f"Missing newline after Jinja2 override: {Templar._repr_from(template)}")

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

    def derived(self, locals: t.Optional[t.Dict[str, t.Any]] = None) -> Context:
        # this is a clone of Jinja's impl of derived, but using our lazy-aware _new_context

        context = _new_context(
            self.environment, self.name, {}, self.get_all(), True, None, locals
        )
        context.eval_ctx = self.eval_ctx
        context.blocks.update((k, list(v)) for k, v in self.blocks.items())
        return context


class AnsibleTemplate(Template):
    """
    A helper class, which prevents Jinja2 from running lazy containers through dict().
    """

    _source_tempfile = None

    # FIXME: this still isn't working reliably; something else must be keeping the template object alive
    def __del__(self):
        if self._source_tempfile:
            os.unlink(self._source_tempfile.name)

    def new_context(
        self,
        vars: c.Mapping[str, t.Any] | None = None,
        shared: bool = False,
        locals: c.Mapping[str, t.Any] | None = None,
    ) -> Context:
        return _new_context(self.environment, self.name, self.blocks, vars, shared, self.globals, locals)


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
    # prevent Jinja's code generation from stringifying single nodes before generating its repr
    # (this complements the behavioral change in our concat)
    # FIXME: contribute this back upstream as a fix to Jinja's native support?
    def _output_const_repr(self, group: t.Iterable[t.Any]) -> str:
        group_list = list(group)

        if len(group_list) == 1:
            return repr(group_list[0])
        return repr("".join(map(str, group_list)))

    # this override causes embedded inline template strings to proxied or rendered at runtime
    # so that some inline templates can be processed with multiple passes, eg, {{ lookup("file", "{{output_dir}}/bla") }}
    def visit_Const(self, node: Const, frame: Frame) -> None:
        value = node.as_const(frame.eval_ctx)

        if type(value) is str and is_possibly_template(value, _TEMPLATE_OVERRIDE_DEFAULT):  # pylint: disable=unidiomatic-typecheck
            # FIXME: propagate other tags from parent template (for forensic/debug)?
            # FIXME: if lookup nerfing is restored, this could end up assigning trust to an embedded constant we don't want to trust.
            #        Keep this note until we're sure it's not coming back.
            self.write(f'environment._access_const({value!r})')
        else:
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

        # Jinja environment's mapping of known names (initially just J2 builtins)
        self._delegatee = delegatee

        # our names take precedence over Jinja's, but let things we've tried to resolve skip the pluginloader
        self._seen_it: set[str] = set()

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
        except KeyError:
            self._seen_it.remove(key)
            plugin_type = self._pluginloader.type
            raise AnsibleTemplatePluginNotFoundError(f'{plugin_type} plugin {key!r} not found') from None

        # FIXME: can/should we handle this in finalize instead, or at least allow plugins to opt into/out of this behavior?
        # if i do have func and it is a filter, it needs wrapping
        if self._pluginloader.type == 'filter':
            # deprecated: description="deprecate STRING_TYPE_FILTERS config entry (formerly used here) once 2.18 is EOL" core_version="2.19"
            # conditionally unroll iterators/generators to avoid having to use `|list` after every filter
            func = _unroll_iterator(func)

            # FIXME: we should probably be running the result of filter plugins through proxy_or_render_template

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
@pass_context
def _ansible_finalize(ctx, thing):
    """
    This function is called by Jinja with the result of each
    variable template block (eg {{ }}) encountered in a template. It
    converts iterator results into lists.
    """

    if _is_rolled(thing):
        # FIXME: make sure this handles lazy lists properly
        thing = list(thing)

#    thing = TemplateContext.current_or_raise().templar.proxy_or_render_template(thing)

    return thing


@dataclasses.dataclass(kw_only=True)
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

    # def call_compare(self, left, *args) -> bool:
    #     left = TemplateContext.current_or_raise().templar.proxy_or_render_template(left)
    #

    # def call_binop(
    #     self, context: Context, operator: str, left: t.Any, right: t.Any
    # ) -> t.Any:
    #     left = TemplateContext.current_or_raise().templar.proxy_or_render_template(left)
    #     right = TemplateContext.current_or_raise().templar.proxy_or_render_template(right)
    #
    #     return super().call_binop(context, operator, left, right)

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

    def concat(self, nodes: t.Iterable[t.Any]) -> t.Any:  # type: ignore[override]
        node_list = list(_flatten_nodes(nodes))

        if not node_list:
            return None

        # this code is complemented by our tweaked CodeGenerator _output_const_repr that ensures that literal constants
        # in templates aren't double-repr'd in the generated code
        if len(node_list) == 1:
            # FIXME: determine if we should do managed access here (we *should* have hit them all during templating/resolve, but ?)
            return node_list[0]

        # FIXME: need to smuggle undefined_behavior in from the current templating operation (eg, debug and templated task names w/ BestEffort)
        # in order to ensure that all embedded triggers fire (vaultbomb, undefined, etc), do a recursive finalize before we repr (otherwise we can end up
        # repr'ing Undefineds etc). Yes, this requires two passes, but means we don't need to have a parallel reimplementation of all reprs
        node_list = _finalize_template_result(node_list, raise_on_unsupported_type=False)

        return ''.join([to_text(v) for v in node_list])

    @staticmethod
    def _access_const(const_template: t.LiteralString) -> t.Any:
        tags: list[AnsibleDatatagBase] = [_JinjaConstTemplate()]
        # FIXME: do we want to propagate source tags here, since this hook may go away?
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
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getitem(obj, argument), argument)

    def getattr(self, obj: t.Any, attribute: str) -> t.Any:
        # example: "{{ some.thing }}" -- obj is the "some" dict, argument is "thing"
        # access on the result of super().getattr is necessary
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getattr(obj, attribute), attribute)

    def call(
        self,  # noqa: B902
        context: Context,
        obj: t.Any,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> t.Any:
        tc = TemplateContext.current_or_raise()
        port = tc.templar.proxy_or_render_template

        # FUTURE: this doesn't scale well- as we add more globals that need special handling, we may want to move that down into the globals
        if obj == tc.templar._lookup or obj == tc.templar._query_lookup:  # we can't use reference equality here; bound methods differ by instance
            with _JinjaConstToTrustedTemplate():
                res = super().call(context, obj, args[0], *port(args[1:]), **port(kwargs))
        else:
            res = super().call(context, obj, *port(args), **port(kwargs))

        return port(res)

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


def _is_rolled(value):
    """Helper method to determine if something is an unrolled generator,
    iterator, or similar object
    """
    return (
        isinstance(value, c.Iterator) or
        isinstance(value, c.MappingView) or
        isinstance(value, RANGE_TYPE)
    )


def _unroll_iterator(func):
    """Wrapper function, that intercepts the result of a templating
    and auto unrolls a generator, so that users are not required to
    explicitly use ``|list`` to unroll.
    """
    def wrapper(*args, **kwargs):
        # FIXME: blind PorRTing *args/**kwargs tries to wrap Jinja context/eval_ctx and other unknown objects; causes a type warning
        port = TemplateContext.current_or_raise().templar.proxy_or_render_template
        ret = func(*port(args), **port(kwargs))
        if _is_rolled(ret):
            ret = list(ret)
        return port(ret)

    return functools.update_wrapper(wrapper, func)


def _flatten_nodes(nodes: t.Iterable[t.Any]) -> t.Iterable[t.Any]:
    """
    Yield nodes from a potentially recursive iterable of nodes.
    The recursion is required to expand template imports (TemplateModule).
    Any UndefinedError exception encountered will be converted to an AnsibleUndefined instance.
    """
    try:
        for node in nodes:
            if type(node) is TemplateModule:  # pylint: disable=unidiomatic-typecheck
                yield from _flatten_nodes(node._body_stream)

            yield node
    except UndefinedError as ex:
        # Convert an UndefinedError generated internally by Jinja2 back into an AnsibleUndefined instance.
        # This instance may be embedded in a data structure and will be subject to UndefinedBehavior handling during template finalization.
        # FIXME: figure out what we should be setting here for obj, key, etc.
        yield AnsibleUndefined(
            template_source=TemplateContext.current_or_raise().template_value,
            hint=ex.message,
        )


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
    environment: Environment,
    template_name: str | None,
    blocks: dict[str, t.Callable[[Context], c.Iterator[str]]],
    vars: c.Mapping[str, t.Any] | None = None,
    shared: bool = False,
    globals: c.MutableMapping[str, t.Any] | None = None,
    locals: c.Mapping[str, t.Any] | None = None,
) -> Context:
    """Override Jinja's context vars setup to use ChainMaps and containers that support lazy templating."""
    layers = []

    if locals:
        # FIXME: if we can't trip this in coverage, kill it off?
        if type(locals) is not dict:  # pylint: disable=unidiomatic-typecheck
            raise NotImplementedError("locals must be a dict")

        # Omit values set to Jinja's internal `missing` sentinel; they are locals that have not yet been
        # initialized in the current context, and should not be exposed to child contexts. e.g.: {% import 'a' as b with context %}.
        # The `b` local will be `missing` in the `a` context and should not be propagated as a local to the child context we're creating.
        layers.append(_AnsibleLazyTemplateMixin.try_create({k: v for k, v in locals.items() if v is not missing}))

    if vars:
        layers.extend(_flatten_and_lazify_vars(vars))

    if globals and not shared:
        # Even though we don't currently support templating globals, it's easier to ensure that everything is template-able rather than trying to
        # pick apart the ChainMaps to enforce non-template-able globals, or to risk things that *should* be template-able not being lazified.
        layers.extend(_flatten_and_lazify_vars(globals))

    # only return a ChainMap if we're combining layers or we have none
    parent = layers[0] if len(layers) == 1 else ChainMap(*layers)

    # the `parent` cast is only to satisfy Jinja's overly-strict type hint
    return environment.context_class(environment, t.cast(dict, parent), template_name, blocks, globals=globals)


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
