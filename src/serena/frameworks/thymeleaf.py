"""
Parsing of Thymeleaf HTML templates.

Only the *model-binding expressions* are extracted: the root variable names referenced
via ``th:*`` attributes (e.g. the ``user`` in ``${user.name}``) together with the
``th:object`` selection context that ``*{...}`` expressions resolve against. Static HTML
structure, CSS classes (including Tailwind utility classes) and text nodes are
deliberately ignored, since they are not part of any binding and would only waste tokens.

An HTML parser (BeautifulSoup with the standard-library ``html.parser``) is appropriate
here because the artifacts genuinely are HTML; no extra dependency is required.

Known limitations (intentionally out of scope for the initial milestone):

* Iteration variables introduced by ``th:each`` (e.g. ``item`` in
  ``th:each="item : ${items}"``) are *local* variables, not model attributes; this parser
  excludes them from the references it reports (only the iterated collection ``items`` is
  a model attribute). Variables introduced by ``th:with`` are likewise local but, unlike
  ``th:each``, are not currently tracked for exclusion in nested expressions.
* Expression utility objects (``#dates``, ``#strings``, ...) and link expressions
  (``@{...}``) are not treated as model-attribute references.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, Optional

from bs4 import BeautifulSoup, Tag

# Thymeleaf standard dialect attribute prefix
_TH_PREFIX = "th:"

# the th:object attribute establishes the selection context for *{...} expressions
_TH_OBJECT_ATTR = "th:object"

# the th:each attribute introduces a local iteration variable that must not be reported
# as a model attribute
_TH_EACH_ATTR = "th:each"


@dataclass(frozen=True)
class TemplateExpressionReference:
    """a reference to a model attribute (root variable) found in a Thymeleaf expression"""

    root_variable: str
    """the root variable name referenced, e.g. ``user`` for the expression ``${user.name}``"""
    line: int
    """the 0-based line number of the element carrying the expression"""
    is_selection: bool
    """
    whether the reference originates from a selection expression (``*{...}``), in which
    case the root variable is taken from the enclosing ``th:object`` rather than the
    expression itself
    """


@dataclass(frozen=True)
class TemplateFile:
    """the parsed model-binding references of a single Thymeleaf HTML template"""

    relative_path: str
    """the path of the template file relative to the project root"""
    references: tuple[TemplateExpressionReference, ...]
    """the model-attribute references found in the template"""

    def root_variables(self) -> frozenset[str]:
        """:return: the distinct root variable names referenced in the template"""
        return frozenset(reference.root_variable for reference in self.references)


class ThymeleafTemplateParser:
    """
    parses Thymeleaf HTML templates, extracting references to model attributes from
    ``${...}`` variable expressions and ``*{...}`` selection expressions while ignoring
    static markup and local iteration variables
    """

    # matches the root identifier at the start of a variable expression ${root...}; the
    # root is the part bound to a model attribute
    _VARIABLE_EXPR: ClassVar[re.Pattern[str]] = re.compile(r"\$\{\s*([A-Za-z_$][\w$]*)")
    # matches the presence of a selection expression *{...}
    _SELECTION_EXPR: ClassVar[re.Pattern[str]] = re.compile(r"\*\{\s*[A-Za-z_$]")
    # matches the iteration source of a th:each value, i.e. the "items" in
    # "item : ${items}" and the loop variable "item" before the colon
    _EACH_EXPR: ClassVar[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*(?:,\s*[A-Za-z_$][\w$]*\s*)?:")

    # a Thymeleaf template is identified by the presence of any th:* attribute; this cheap
    # pre-check avoids fully parsing plain HTML files
    _TH_PRECHECK: ClassVar[re.Pattern[str]] = re.compile(r"\bth:[a-zA-Z-]+\s*=", re.IGNORECASE)

    def is_candidate(self, file_content: str) -> bool:
        """
        :param file_content: the raw content of an HTML file
        :return: whether the file looks like a Thymeleaf template (cheap pre-check)
        """
        return self._TH_PRECHECK.search(file_content) is not None

    def parse(self, relative_path: str, file_content: str) -> TemplateFile:
        """
        Parses a Thymeleaf HTML template into its model-binding references.

        :param relative_path: the path of the file relative to the project root
        :param file_content: the raw content of the file
        :return: the parsed template file (with an empty reference tuple if no bindings
            are found)
        """
        soup = BeautifulSoup(file_content, "html.parser")

        references: list[TemplateExpressionReference] = []
        for tag in soup.find_all(True):
            references.extend(self._extract_references_from_tag(tag))

        return TemplateFile(relative_path=relative_path, references=tuple(references))

    def _extract_references_from_tag(self, tag: Tag) -> list[TemplateExpressionReference]:
        """
        :param tag: an HTML element
        :return: the model-attribute references carried by the element's ``th:*`` attributes
        """
        references: list[TemplateExpressionReference] = []
        line = (tag.sourceline or 1) - 1

        # collect local iteration variables declared on this element so that they are not
        # misreported as model attributes when they reappear in other expressions
        local_variables = self._extract_each_local_variables(tag)

        # a th:object on this element binds its root variable directly to a model attribute
        own_object_root = self._extract_own_object_root(tag)
        if own_object_root is not None and own_object_root not in local_variables:
            references.append(TemplateExpressionReference(root_variable=own_object_root, line=line, is_selection=False))

        # selection expressions (*{...}) resolve against the nearest enclosing th:object,
        # which may be on this element or any ancestor
        selection_context = own_object_root if own_object_root is not None else self._resolve_ancestor_object_root(tag)

        # extract variable and selection roots from all th:* attributes (except th:object,
        # which is handled above)
        for attr_name, attr_value in tag.attrs.items():
            attr_name_lower = attr_name.lower()
            if not attr_name_lower.startswith(_TH_PREFIX) or attr_name_lower == _TH_OBJECT_ATTR:
                continue
            value_str = attr_value if isinstance(attr_value, str) else " ".join(attr_value)
            references.extend(
                self._extract_references_from_value(
                    value_str,
                    line,
                    selection_context,
                    local_variables,
                    is_each_attr=attr_name_lower == _TH_EACH_ATTR,
                )
            )

        return references

    def _extract_each_local_variables(self, tag: Tag) -> frozenset[str]:
        """
        :param tag: an HTML element
        :return: the local iteration variable names declared by the element's ``th:each``
            (both the iteration variable and the optional status variable), or an empty set
        """
        raw = tag.get(_TH_EACH_ATTR)
        if not isinstance(raw, str):
            return frozenset()

        # th:each has the form "var[, status] : ${collection}"; the names before the colon
        # are local variables
        head, _, _ = raw.partition(":")
        names = {part.strip() for part in head.split(",") if part.strip()}
        return frozenset(names)

    def _extract_own_object_root(self, tag: Tag) -> Optional[str]:
        """
        :param tag: an HTML element
        :return: the root variable of the element's own ``th:object`` value, or ``None`` if
            the element has no (parsable) ``th:object``
        """
        # only the th:object attribute establishes a selection context
        raw = tag.get(_TH_OBJECT_ATTR)
        if not isinstance(raw, str):
            return None
        match = self._VARIABLE_EXPR.search(raw)
        return match.group(1) if match else None

    def _resolve_ancestor_object_root(self, tag: Tag) -> Optional[str]:
        """
        :param tag: an HTML element
        :return: the root variable of the nearest ancestor's ``th:object``, or ``None`` if
            no ancestor establishes a selection context (a ``th:object`` applies to the
            entire subtree below the element carrying it)
        """
        # walk up the ancestor chain until a th:object is found
        for parent in tag.parents:
            ancestor_root = self._extract_own_object_root(parent)
            if ancestor_root is not None:
                return ancestor_root
        return None

    def _extract_references_from_value(
        self,
        value: str,
        line: int,
        object_root: Optional[str],
        local_variables: frozenset[str],
        is_each_attr: bool,
    ) -> list[TemplateExpressionReference]:
        """
        :param value: the raw value of a ``th:*`` attribute
        :param line: the 0-based line number of the element carrying the attribute
        :param object_root: the root variable of the enclosing ``th:object``, if any
        :param local_variables: local variable names that must not be reported as model
            attributes
        :param is_each_attr: whether the attribute is a ``th:each`` (whose loop variable,
            appearing before the colon, must not be treated as a model attribute)
        :return: the model-attribute references found in the attribute value
        """
        references: list[TemplateExpressionReference] = []

        # for th:each, only the iterated collection (inside ${...}) is a model attribute;
        # the loop variable before the colon is excluded by construction since it is not
        # written as a ${...} expression
        _ = is_each_attr

        # variable expressions ${root...} reference a model attribute directly, unless the
        # root is a local (iteration) variable
        for match in self._VARIABLE_EXPR.finditer(value):
            root_variable = match.group(1)
            if root_variable in local_variables:
                continue
            references.append(TemplateExpressionReference(root_variable=root_variable, line=line, is_selection=False))

        # selection expressions *{...} reference a property of the th:object context; they
        # only bind to a model attribute if such a context is present
        if object_root is not None and object_root not in local_variables and self._SELECTION_EXPR.search(value) is not None:
            references.append(TemplateExpressionReference(root_variable=object_root, line=line, is_selection=True))

        return references
