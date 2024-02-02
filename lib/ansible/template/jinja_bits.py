from __future__ import annotations

import collections.abc as c
import datetime
import functools
from collections import ChainMap
from typing import MutableMapping, Iterator, MappingView

from jinja2 import pass_context
from jinja2.environment import TemplateModule, Environment
from jinja2.exceptions import TemplateSyntaxError, UndefinedError
from jinja2.runtime import Undefined
from jinja2.compiler import Frame
from jinja2.nativetypes import NativeTemplate, NativeCodeGenerator
from jinja2.nodes import Const, Dict, List, Tuple
from jinja2.runtime import Context
from jinja2.sandbox import ImmutableSandboxedEnvironment
from jinja2.utils import missing

from ansible.utils.display import Display
from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_text, to_native
from ansible.module_utils.compat import typing as t
from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.module_utils.datatag.access import AnsibleAccessContext
from ansible.module_utils.six import string_types
from ansible.plugins.loader import filter_loader, test_loader

from .utils import AnsibleUndefined, Omit, TemplateContext
from .lazy_containers import _finalize_template_result, _AnsibleLazyTemplateMixin, _AnsibleLazyTemplateDict

RANGE_TYPE = type(range(0))

display = Display()


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


class AnsibleTemplate(NativeTemplate):
    """
    A helper class, which prevents Jinja2 from running lazy containers through dict().
    """

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
            self.write(str(val))  # FIXME: why is this not just using repr(val) below?
        elif is_template:
            # FIXME: propagate other tags from parent template (for forensic/debug)?
            # FIXME: if lookup nerfing is restored, this could end up assigning trust to an embedded constant we don't want to trust.
            #  Keep this note until we're sure it's not coming back.
            self.write(f'environment._render_const_template({val!r})')
        else:
            self.write(repr(val))


class JinjaPluginIntercept(MutableMapping):
    """
    Simulated dict class that loads Jinja2Plugins at request
    otherwise all plugins would need to be loaded a priori.

    NOTE: plugin_loader still loads all 'builtin/legacy' at
    start so only collection plugins are really at request.
    """

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

    return thing


class AnsibleEnvironment(ImmutableSandboxedEnvironment):
    """
    Our custom environment, which simply allows us to override the class-level
    values for the Template and Context classes used by jinja2 internally.
    """
    context_class = AnsibleContext
    template_class = AnsibleTemplate
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
    def _render_const_template(const_template: t.LiteralString) -> t.Any:
        """
        This method is for exclusive use by the template compiler to render embedded constant templates.
        Since these values may be stored in locals that will receive no further processing before use, they must be trusted and templated, not just trusted.
        """
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(TrustedAsTemplate().tag(const_template))

    def getitem(self, obj: t.Any, argument: t.Any) -> t.Any:
        # FIXME: do we actually need to managed-access both sides of templates/strings here?
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getitem(obj, argument), argument)

    def getattr(self, obj: t.Any, attribute: str) -> t.Any:
        return TemplateContext.current_or_raise().templar.proxy_or_render_template(super().getattr(obj, attribute), attribute)

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


def _new_context(
    environment: Environment,
    template_name: t.Optional[str],
    blocks: t.Dict[str, t.Callable[[Context], t.Iterator[str]]],
    vars: t.Optional[t.Mapping[str, t.Any]] = None,
    shared: bool = False,
    globals: t.Optional[t.MutableMapping[str, t.Any]] = None,
    locals: t.Optional[t.Mapping[str, t.Any]] = None,
) -> Context:
    layers = []

    # FIXME: add docstring

    if locals:
        # FIXME: if we can't trip this in coverage, kill it off?
        if type(locals) is not dict:  # pylint: disable=unidiomatic-typecheck
            raise NotImplementedError("locals must be a dict")

        if missing in locals.values():
            # FIXME: if we can't trip this in coverage, kill it off?
            raise NotImplementedError("missing value encountered in template local")

        layers.append(_AnsibleLazyTemplateMixin.try_create(locals))

    if vars:
        # deal with vars being a chainmap
        if isinstance(vars, ChainMap):
            # FIXME: should we verify that these are already lazy?
            if any(type(v) is not _AnsibleLazyTemplateDict for v in vars.maps):  # pylint: disable=unidiomatic-typecheck
                raise NotImplementedError("non-lazy layer in vars ChainMap is not implemented")
            layers.extend(vars.maps)
        elif type(vars) in (dict, _AnsibleLazyTemplateDict):
            layers.append(_AnsibleLazyTemplateMixin.try_create(vars))
        else:
            raise NotImplementedError(f"non-dict Jinja Context vars not supported, {type(vars)=}")

    if globals and not shared:
        # FIXME: this should probably be lazy as well
        layers.append(globals)

    # only return a ChainMap if we're combining layers or we have none
    parent = layers[0] if len(layers) == 1 else ChainMap(*layers)

    # the `parent` cast is only to satisfy Jinja's overly-strict type hint
    return environment.context_class(environment, t.cast(dict, parent), template_name, blocks, globals=globals)
