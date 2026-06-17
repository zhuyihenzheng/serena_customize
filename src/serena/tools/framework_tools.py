"""
Optional tools for framework-aware cross-artifact navigation.

These tools expose the bidirectional bindings computed by
:class:`~serena.frameworks.binding_index.FrameworkBindingIndex`:

* MyBatis: navigation between a Java mapper interface method and the ``<select>`` /
  ``<insert>`` / ``<update>`` / ``<delete>`` statement that implements it.
* Thymeleaf: navigation between a controller-provided model attribute and the HTML
  template expressions that reference it.

All tools are marked :class:`~serena.tools.ToolMarkerOptional` and are therefore disabled by
default; they must be enabled per project via ``included_optional_tools`` in the project
configuration (mirroring how the JetBrains-backend tools are gated).
"""

import re
from typing import ClassVar, Optional

from serena.frameworks.binding_index import FrameworkBindingIndex
from serena.tools import Tool, ToolMarkerOptional
from serena.tools.tools_base import ToolMarkerSymbolicRead

# matches the package declaration at the top of a Java source file
_JAVA_PACKAGE_DECLARATION: re.Pattern[str] = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _extract_java_package(file_content: str) -> Optional[str]:
    """
    :param file_content: the content of a Java source file
    :return: the declared package name, or ``None`` for a file in the default package
    """
    match = _JAVA_PACKAGE_DECLARATION.search(file_content)
    return match.group(1) if match else None


def _qualified_method_id(package: Optional[str], interface_simple_name: str, method_name: str) -> str:
    """
    :param package: the Java package of the mapper interface, or ``None`` for the default package
    :param interface_simple_name: the simple (unqualified) name of the mapper interface
    :param method_name: the mapper method name
    :return: the fully qualified statement id, i.e. ``package.Interface.method`` (or
        ``Interface.method`` if there is no package)
    """
    qualified_interface = f"{package}.{interface_simple_name}" if package else interface_simple_name
    return f"{qualified_interface}.{method_name}"


class FindMapperXmlForMethodTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Finds the MyBatis mapper XML statement bound to a Java mapper interface method.
    """

    def apply(self, relative_path: str, method_name: str, max_answer_chars: int = -1) -> str:
        """
        Finds the MyBatis XML statement (``<select>``/``<insert>``/``<update>``/``<delete>``)
        that implements the given Java mapper interface method, returning the XML file and
        line number so it can be opened directly.

        This resolves a binding that a Java language server cannot see, because the XML
        statement lives in a separate file that the language server treats as opaque.

        :param relative_path: the project-relative path of the Java mapper *interface* file
            (the ``.java`` file declaring the method, e.g.
            ``src/main/java/com/example/mapper/UserMapper.java``)
        :param method_name: the name of the mapper method (e.g. ``findById``)
        :param max_answer_chars: max result length; -1 for default
        :return: a description of the bound XML statement (path, line, statement type), or a
            message indicating that no binding was found
        """
        # derive the fully qualified mapper id from the Java file's package declaration and
        # the interface name (the file's base name)
        file_content = self.project.read_file(relative_path)
        package = _extract_java_package(file_content)
        interface_simple_name = self._interface_name_from_path(relative_path)
        qualified_method_id = _qualified_method_id(package, interface_simple_name, method_name)

        # build the binding index and look up the statement bound to the method
        index = FrameworkBindingIndex.build(self.project)
        statement_location = index.find_statement_for_qualified_method(qualified_method_id)

        if statement_location is None:
            result = (
                f"No MyBatis mapper statement found for method '{qualified_method_id}'.\n"
                f"Checked {len(index.iter_mapper_files())} mapper file(s). "
                f"Ensure the mapper XML's <mapper namespace> matches the fully qualified interface name "
                f"and that a <select>/<insert>/<update>/<delete> with id='{method_name}' exists."
            )
            return self._limit_length(result, max_answer_chars)

        result = self._to_json(
            {
                "method": qualified_method_id,
                "statement_type": statement_location.statement.statement_type,
                "relative_path": statement_location.relative_path,
                "line": statement_location.line,
            }
        )
        return self._limit_length(result, max_answer_chars)

    @staticmethod
    def _interface_name_from_path(relative_path: str) -> str:
        """
        :param relative_path: the project-relative path of a Java file
        :return: the simple interface/class name (the file's base name without extension)
        """
        base = relative_path.replace("\\", "/").rsplit("/", 1)[-1]
        return base[:-5] if base.endswith(".java") else base


class FindMapperMethodForXmlTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Finds the Java mapper interface method bound to a MyBatis XML statement.
    """

    def apply(self, relative_path: str, statement_id: str, max_answer_chars: int = -1) -> str:
        """
        Finds the Java mapper interface method that the given MyBatis XML statement
        implements, using the mapper's ``namespace`` (the fully qualified interface name)
        together with the statement ``id`` (the method name).

        This resolves the reverse of the binding that a Java language server cannot see.

        :param relative_path: the project-relative path of the MyBatis mapper *XML* file
            (e.g. ``src/main/resources/mapper/UserMapper.xml``)
        :param statement_id: the ``id`` of the statement to resolve (e.g. ``findById``)
        :param max_answer_chars: max result length; -1 for default
        :return: a description of the bound Java method (its fully qualified id and the
            mapper namespace), or a message indicating that no binding was found
        """
        # build the index and locate the mapper that corresponds to the given XML file
        index = FrameworkBindingIndex.build(self.project)
        normalised_path = relative_path.replace("\\", "/")
        mapper_file = next(
            (mapper for mapper in index.iter_mapper_files() if mapper.relative_path.replace("\\", "/") == normalised_path),
            None,
        )

        if mapper_file is None:
            result = f"No MyBatis mapper recognised at '{relative_path}'. Ensure the file has a <mapper namespace=\"...\"> root."
            return self._limit_length(result, max_answer_chars)

        statement = mapper_file.find_statement(statement_id)
        if statement is None:
            available = ", ".join(s.statement_id for s in mapper_file.statements) or "(none)"
            result = (
                f"Mapper '{mapper_file.namespace}' declares no statement with id='{statement_id}'.\nAvailable statement ids: {available}"
            )
            return self._limit_length(result, max_answer_chars)

        # the bound Java method is namespace.statementId; report it so the caller can locate
        # the interface method via the standard symbol tools
        result = self._to_json(
            {
                "mapper_namespace": mapper_file.namespace,
                "statement_id": statement.statement_id,
                "statement_type": statement.statement_type,
                "java_method_id": statement.qualified_id(mapper_file.namespace),
                "name_path_in_interface": f"{mapper_file.namespace.rsplit('.', 1)[-1]}/{statement.statement_id}",
            }
        )
        return self._limit_length(result, max_answer_chars)


class FindThymeleafBindingsForModelAttributeTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Finds Thymeleaf template expressions that reference a given model attribute.
    """

    def apply(self, model_attribute: str, max_answer_chars: int = -1) -> str:
        """
        Finds the Thymeleaf template locations that reference the given model attribute (a
        root variable used in ``${...}`` expressions, or the ``th:object`` root that
        ``*{...}`` selection expressions resolve against).

        This resolves a cross-language (HTML -> Java) binding that a Java language server
        cannot see, since the templates are HTML files that the language server does not
        analyse.

        :param model_attribute: the model-attribute name to search for (e.g. ``user`` for
            references such as ``${user.name}``); this is the name a controller passes to
            ``model.addAttribute("user", ...)``
        :param max_answer_chars: max result length; -1 for default
        :return: the template references to the model attribute (path, line, whether the
            reference is a selection expression), or a message indicating that none were found
        """
        # build the index and look up template references to the attribute
        index = FrameworkBindingIndex.build(self.project)
        bindings = index.find_template_bindings_for_variable(model_attribute)

        if not bindings:
            result = (
                f"No Thymeleaf template references found for model attribute '{model_attribute}'.\n"
                f"Checked {len(index.iter_template_files())} template file(s)."
            )
            return self._limit_length(result, max_answer_chars)

        result = self._to_json(
            {
                "model_attribute": model_attribute,
                "reference_count": len(bindings),
                "references": [
                    {
                        "relative_path": binding.relative_path,
                        "line": binding.line,
                        "is_selection_expression": binding.reference.is_selection,
                    }
                    for binding in bindings
                ],
            }
        )
        return self._limit_length(result, max_answer_chars)


class ListThymeleafModelAttributesTool(Tool, ToolMarkerSymbolicRead, ToolMarkerOptional):
    """
    Lists the model attributes referenced by a Thymeleaf template.
    """

    # the controller-side context an LLM should consult to confirm where the attributes
    # originate; kept as a hint rather than resolved here to avoid guessing controller code
    _CONTROLLER_HINT: ClassVar[str] = (
        "Each attribute is expected to be supplied to the template by a controller, "
        'typically via Model.addAttribute("<name>", ...) or an @ModelAttribute method; '
        "use the standard symbol tools to locate that controller code."
    )

    def apply(self, relative_path: str, max_answer_chars: int = -1) -> str:
        """
        Lists the distinct model attributes (root variables) that the given Thymeleaf
        template references, so that the controller code providing them can be located with
        the standard symbol tools.

        :param relative_path: the project-relative path of the Thymeleaf *HTML* template
            (e.g. ``src/main/resources/templates/user_detail.html``)
        :param max_answer_chars: max result length; -1 for default
        :return: the distinct model attributes referenced by the template (with the lines on
            which each is referenced), or a message indicating that the file is not a
            recognised Thymeleaf template
        """
        # build the index and locate the parsed template for the given file
        index = FrameworkBindingIndex.build(self.project)
        normalised_path = relative_path.replace("\\", "/")
        template_file = next(
            (template for template in index.iter_template_files() if template.relative_path.replace("\\", "/") == normalised_path),
            None,
        )

        if template_file is None:
            result = f"No Thymeleaf template recognised at '{relative_path}'. Ensure the file contains th:* attributes."
            return self._limit_length(result, max_answer_chars)

        # group the referenced lines per attribute for a compact, navigable result
        lines_by_attribute: dict[str, list[int]] = {}
        for reference in template_file.references:
            lines_by_attribute.setdefault(reference.root_variable, []).append(reference.line)

        result = self._to_json(
            {
                "relative_path": template_file.relative_path,
                "model_attributes": [
                    {"name": name, "referenced_on_lines": sorted(set(lines))} for name, lines in sorted(lines_by_attribute.items())
                ],
                "hint": self._CONTROLLER_HINT,
            }
        )
        return self._limit_length(result, max_answer_chars)
