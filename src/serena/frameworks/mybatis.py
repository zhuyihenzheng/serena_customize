"""
Parsing of MyBatis mapper XML files.

Only the *binding skeleton* is extracted: the mapper namespace and the executable
statement identifiers together with their line numbers. SQL bodies, ``<sql>`` fragments
and dynamic tags (``<if>``, ``<foreach>``, ...) are deliberately ignored, since they are
not needed for navigation and would only waste tokens.

The parser uses the standard library's ``xml.parsers.expat`` rather than an HTML parser:
mapper XML is case-sensitive and an HTML parser would fold tag names to lowercase, and
expat additionally provides accurate line numbers and handles the ``DOCTYPE``/DTD
declaration that mapper files typically carry. This avoids introducing ``lxml`` as a new
runtime dependency.
"""

import xml.parsers.expat
from dataclasses import dataclass
from typing import ClassVar, Optional

# the MyBatis XML tags that declare an executable statement bound to a mapper method
_STATEMENT_TAGS = frozenset({"select", "insert", "update", "delete"})


@dataclass(frozen=True)
class MapperStatement:
    """
    a single executable statement declared in a MyBatis mapper XML file, bound to a Java
    mapper interface method of the same name
    """

    statement_id: str
    """the value of the statement's ``id`` attribute (the method name it binds to)"""
    statement_type: str
    """the kind of statement, one of ``select``/``insert``/``update``/``delete``"""
    line: int
    """the 0-based line number of the statement's opening tag in the XML file"""

    def qualified_id(self, namespace: str) -> str:
        """
        :param namespace: the mapper namespace (the fully qualified interface name)
        :return: the fully qualified statement id, i.e. ``namespace.statement_id``
        """
        return f"{namespace}.{self.statement_id}"


@dataclass(frozen=True)
class MapperFile:
    """the parsed binding skeleton of a single MyBatis mapper XML file"""

    relative_path: str
    """the path of the mapper XML file relative to the project root"""
    namespace: str
    """the mapper namespace, i.e. the fully qualified Java mapper interface name"""
    statements: tuple[MapperStatement, ...]
    """the executable statements declared in the file"""

    def find_statement(self, statement_id: str) -> Optional[MapperStatement]:
        """
        :param statement_id: the (unqualified) statement id to look up
        :return: the statement with the given id, or ``None`` if no such statement exists
        """
        # a mapper may in principle declare the same id more than once (across statement
        # types); the first declaration is the canonical navigation target
        for statement in self.statements:
            if statement.statement_id == statement_id:
                return statement
        return None


class MyBatisMapperParser:
    """
    parses MyBatis mapper XML files into their binding skeleton, extracting the namespace
    and the executable statements while ignoring SQL bodies
    """

    # a mapper XML is identified by the presence of a "<mapper namespace=" opening; this
    # cheap substring pre-check avoids invoking the XML parser on unrelated XML files
    _NAMESPACE_PRECHECK: ClassVar[str] = "<mapper"

    def is_candidate(self, file_content: str) -> bool:
        """
        :param file_content: the raw content of an XML file
        :return: whether the file looks like a MyBatis mapper XML file (cheap pre-check)
        """
        # require both the mapper tag and a namespace attribute to be present somewhere
        return self._NAMESPACE_PRECHECK in file_content and "namespace" in file_content

    def parse(self, relative_path: str, file_content: str) -> Optional[MapperFile]:
        """
        Parses a MyBatis mapper XML file into its binding skeleton.

        :param relative_path: the path of the file relative to the project root
        :param file_content: the raw content of the file
        :return: the parsed mapper file, or ``None`` if the file is not a MyBatis mapper
            (i.e. it has no ``<mapper namespace="...">`` root) or could not be parsed
        """
        # cheap rejection of non-mapper XML before invoking the parser
        if not self.is_candidate(file_content):
            return None

        # accumulate the namespace and statements via an expat parser; we record the
        # namespace from the <mapper> element and the id/type/line of each statement,
        # ignoring all other content (SQL bodies, <sql>, dynamic tags, ...)
        namespace_holder: list[str] = []
        statements: list[MapperStatement] = []

        def handle_start_element(name: str, attributes: dict[str, str]) -> None:
            if name == "mapper":
                namespace = attributes.get("namespace", "").strip()
                if namespace:
                    namespace_holder.append(namespace)
            elif name in _STATEMENT_TAGS:
                statement_id = attributes.get("id", "").strip()
                if statement_id:
                    statements.append(
                        MapperStatement(
                            statement_id=statement_id,
                            statement_type=name,
                            # expat reports 1-based line numbers; normalise to 0-based
                            line=max(parser.CurrentLineNumber - 1, 0),
                        )
                    )

        parser = xml.parsers.expat.ParserCreate()
        parser.StartElementHandler = handle_start_element

        # a malformed mapper file should not crash indexing; treat it as "not a mapper"
        try:
            parser.Parse(file_content, True)
        except xml.parsers.expat.ExpatError:
            return None

        # without a namespace there is nothing to bind Java methods to
        if not namespace_holder:
            return None

        return MapperFile(relative_path=relative_path, namespace=namespace_holder[0], statements=tuple(statements))
