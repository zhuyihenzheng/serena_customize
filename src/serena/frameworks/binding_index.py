"""
The framework binding index.

This module ties together the binding skeletons parsed from non-Java artifacts (MyBatis
mapper XML, Thymeleaf HTML) and the Java symbols obtained from the language server, so
that navigation can cross the artifact boundary in both directions.

The index itself is responsible only for parsing the non-Java artifacts and building the
lookup tables over them. Resolving the *Java* side of a binding (a mapper interface method
or a controller-provided model attribute) is delegated to the
:class:`~serena.symbol.LanguageServerSymbolRetriever`, so that the existing language server
(e.g. Eclipse JDT.LS) remains the single source of truth for Java symbols and no Java
parsing is reimplemented here.

The index is built lazily from a project scan and is not cached across tool invocations;
caching can be added later if scanning proves to be a performance concern.
"""

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from serena.frameworks.mybatis import MapperFile, MapperStatement, MyBatisMapperParser
from serena.frameworks.thymeleaf import TemplateExpressionReference, TemplateFile, ThymeleafTemplateParser

if TYPE_CHECKING:
    from serena.project import Project

log = logging.getLogger(__name__)

# the file extensions that may carry framework bindings; these are scanned in addition to
# (and independently of) the project's configured source-file languages, since mapper XML
# and Thymeleaf HTML are frequently not registered as source files of any language server
_MAPPER_XML_EXTENSION = ".xml"
_TEMPLATE_HTML_EXTENSIONS = (".html", ".htm")


@dataclass(frozen=True)
class MapperStatementLocation:
    """the location of a MyBatis statement, i.e. the XML side of a mapper binding"""

    mapper_file: MapperFile
    """the parsed mapper file that declares the statement"""
    statement: MapperStatement
    """the statement within the mapper file"""

    @property
    def relative_path(self) -> str:
        """:return: the path of the mapper XML file relative to the project root"""
        return self.mapper_file.relative_path

    @property
    def line(self) -> int:
        """:return: the 0-based line number of the statement's opening tag"""
        return self.statement.line


@dataclass(frozen=True)
class TemplateBindingLocation:
    """the location of a Thymeleaf model-attribute reference, i.e. the HTML side of a binding"""

    template_file: TemplateFile
    """the parsed template file that contains the reference"""
    reference: TemplateExpressionReference
    """the model-attribute reference within the template file"""

    @property
    def relative_path(self) -> str:
        """:return: the path of the template file relative to the project root"""
        return self.template_file.relative_path

    @property
    def line(self) -> int:
        """:return: the 0-based line number of the element carrying the expression"""
        return self.reference.line


class FrameworkBindingIndex:
    """
    indexes the framework bindings of a project, providing bidirectional lookup between
    Java symbols and the non-Java artifacts they bind to
    """

    def __init__(
        self,
        project: "Project",
        mapper_files: tuple[MapperFile, ...],
        template_files: tuple[TemplateFile, ...],
    ) -> None:
        """
        :param project: the project the index was built for
        :param mapper_files: the parsed MyBatis mapper files of the project
        :param template_files: the parsed Thymeleaf template files of the project
        """
        self._project = project
        self._mapper_files = mapper_files
        self._template_files = template_files

        # index mapper statements by their fully qualified id (namespace.statementId) for
        # the Java-method -> XML direction, and by namespace for the XML -> Java direction
        self._mapper_by_namespace: dict[str, MapperFile] = {mapper.namespace: mapper for mapper in mapper_files}
        self._statement_by_qualified_id: dict[str, MapperStatementLocation] = {}
        for mapper in mapper_files:
            for statement in mapper.statements:
                qualified_id = statement.qualified_id(mapper.namespace)
                # keep the first declaration as the canonical target on id collisions
                self._statement_by_qualified_id.setdefault(qualified_id, MapperStatementLocation(mapper_file=mapper, statement=statement))

        # index template references by the root model-attribute variable they reference
        self._template_locations_by_variable: dict[str, list[TemplateBindingLocation]] = {}
        for template in template_files:
            for reference in template.references:
                self._template_locations_by_variable.setdefault(reference.root_variable, []).append(
                    TemplateBindingLocation(template_file=template, reference=reference)
                )

    @classmethod
    def build(cls, project: "Project") -> "FrameworkBindingIndex":
        """
        Builds a framework binding index by scanning the project for MyBatis mapper XML and
        Thymeleaf HTML files and parsing their binding skeletons.

        :param project: the project to scan
        :return: the framework binding index
        """
        mapper_parser = MyBatisMapperParser()
        template_parser = ThymeleafTemplateParser()

        mapper_files: list[MapperFile] = []
        template_files: list[TemplateFile] = []

        # scan the project tree directly rather than via gather_source_files, since the
        # latter filters out files that are not source files of a configured language
        # (mapper XML / Thymeleaf HTML usually are not)
        for relative_path in cls._iter_candidate_files(project):
            extension = os.path.splitext(relative_path)[1].lower()

            try:
                content = project.read_file(relative_path)
            except (OSError, UnicodeDecodeError) as e:
                log.debug("Skipping unreadable file %s during framework scan: %s", relative_path, e)
                continue

            # parse mapper XML candidates
            if extension == _MAPPER_XML_EXTENSION:
                if mapper_parser.is_candidate(content):
                    mapper_file = mapper_parser.parse(relative_path, content)
                    if mapper_file is not None:
                        mapper_files.append(mapper_file)

            # parse Thymeleaf HTML candidates
            elif extension in _TEMPLATE_HTML_EXTENSIONS:
                if template_parser.is_candidate(content):
                    template_files.append(template_parser.parse(relative_path, content))

        log.info(
            "Framework binding index built: %d mapper file(s), %d template file(s)",
            len(mapper_files),
            len(template_files),
        )
        return cls(project=project, mapper_files=tuple(mapper_files), template_files=tuple(template_files))

    @staticmethod
    def _iter_candidate_files(project: "Project") -> list[str]:
        """
        :param project: the project to scan
        :return: the project-relative paths of files that may carry framework bindings
            (XML and HTML files that are not ignored via .gitignore)
        """
        candidate_extensions = (_MAPPER_XML_EXTENSION, *_TEMPLATE_HTML_EXTENSIONS)
        relative_paths: list[str] = []

        for root, dirs, files in os.walk(project.project_root, followlinks=True):
            # prune ignored directories (e.g. target/, node_modules/, .git/)
            dirs[:] = [d for d in dirs if not project.is_ignored_path(os.path.join(root, d))]

            for file in files:
                if not file.lower().endswith(candidate_extensions):
                    continue
                absolute_path = os.path.join(root, file)
                # only exclude gitignored files; do NOT exclude non-source files, since the
                # bindings live precisely in files that are not language source files
                if project.is_ignored_path(absolute_path, ignore_non_source_files=False):
                    continue
                try:
                    relative_paths.append(os.path.relpath(absolute_path, start=project.project_root))
                except ValueError:
                    continue

        return relative_paths

    def find_statement_for_qualified_method(self, qualified_method_id: str) -> Optional[MapperStatementLocation]:
        """
        Looks up the MyBatis statement bound to a Java mapper method (the Java -> XML
        direction).

        :param qualified_method_id: the fully qualified method id, i.e.
            ``fullyQualifiedInterfaceName.methodName`` (e.g.
            ``com.example.mapper.UserMapper.findById``)
        :return: the bound statement's location, or ``None`` if no mapper declares it
        """
        return self._statement_by_qualified_id.get(qualified_method_id)

    def find_namespace_mapper(self, namespace: str) -> Optional[MapperFile]:
        """
        :param namespace: a mapper namespace (a fully qualified Java interface name)
        :return: the mapper file with the given namespace, or ``None`` if there is none
        """
        return self._mapper_by_namespace.get(namespace)

    def iter_mapper_files(self) -> tuple[MapperFile, ...]:
        """:return: all parsed mapper files of the project"""
        return self._mapper_files

    def find_template_bindings_for_variable(self, root_variable: str) -> list[TemplateBindingLocation]:
        """
        Looks up the Thymeleaf template references to a given model attribute (the Java ->
        HTML direction).

        :param root_variable: the model-attribute name (root variable) to look up, e.g.
            ``user`` for references such as ``${user.name}``
        :return: the template references to the model attribute (possibly empty), ordered
            by the file scan order
        """
        return list(self._template_locations_by_variable.get(root_variable, ()))

    def iter_template_files(self) -> tuple[TemplateFile, ...]:
        """:return: all parsed template files of the project"""
        return self._template_files
