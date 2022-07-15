import importlib
import inspect
import typing as t

from .message import TaskOptions

C = t.TypeVar('C')


@t.runtime_checkable
class BasePlugin(t.Protocol):
    plugin_options: dict[str, list[str]] = {}
    uses_plugin_type_names: frozenset[str] = frozenset()  # this is temporary and will be replaced with the parsed data from plugin options

    # FIXME: Try and make this better somehow
    ansible_variable_name: str
    ansible_plugin_type: str
    __options: dict[str, t.Any]

    def set_options(self, options: dict[str, ...]) -> None:
        self.__options = options

    def get_option(self, option: str, default: t.Any = None) -> t.Any:
        return self.__options.get(option, default)

    @classmethod
    @property
    def name(cls) -> str:  # noqa
        return cls.__module__.split('.')[-1]

    @classmethod
    @property
    def fqname(cls) -> str:  # noqa
        return cls.__module__


def get_plugin_type(*, plugin_name: str, plugin_type: t.Type[C]) -> t.Type[C]:
    plugin_short_name = plugin_name.split('.')[-1]
    importlib.import_module(plugin_name)
    plugin_types = get_subclasses(plugin_type)
    matching_plugin_types = [plugin_type for plugin_type in plugin_types if plugin_type.name == plugin_short_name]

    assert len(matching_plugin_types) == 1

    matching_plugin_type = matching_plugin_types[0]

    return matching_plugin_type


def get_plugin(*, plugin_name: str, plugin_type: t.Type[C], task_options: TaskOptions, **kwargs: t.Any) -> C:
    matching_plugin_type = get_plugin_type(plugin_name=plugin_name, plugin_type=plugin_type)
    plugin_instance = matching_plugin_type(**kwargs)
    plugin_instance.set_options(task_options.plugin_options[plugin_name])

    return plugin_instance


def get_subclasses(class_type: t.Type[C]) -> list[t.Type[C]]:
    """Returns a list of types that are concrete subclasses of the given type."""
    subclasses: set[t.Type[C]] = set()
    queue: list[t.Type[C]] = [class_type]

    while queue:
        parent = queue.pop()

        for child in parent.__subclasses__():
            if child not in subclasses:
                if not inspect.isabstract(child):
                    subclasses.add(child)
                queue.append(child)

    return sorted(subclasses, key=lambda sc: sc.__name__)
