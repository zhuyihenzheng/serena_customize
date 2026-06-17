"""
Unit tests for the framework artifact parsers (MyBatis mapper XML and Thymeleaf HTML).

These tests are pure-function tests over the parsers and do not require a language server.
They assert concrete statement ids, namespaces, model-attribute names and line numbers (not
merely that some non-empty result was returned), as required by Serena's testing standard.
"""

from pathlib import Path

import pytest

from serena.frameworks.mybatis import MyBatisMapperParser
from serena.frameworks.thymeleaf import ThymeleafTemplateParser

_FIXTURE_REPO = Path(__file__).parent.parent.parent / "resources" / "repos" / "frameworks" / "test_repo"
_MAPPER_XML = _FIXTURE_REPO / "src" / "main" / "resources" / "mapper" / "UserMapper.xml"
_TEMPLATE_HTML = _FIXTURE_REPO / "src" / "main" / "resources" / "templates" / "user_detail.html"


@pytest.mark.frameworks
class TestMyBatisMapperParser:
    def test_namespace_and_statements_are_extracted(self) -> None:
        content = _MAPPER_XML.read_text(encoding="utf-8")
        mapper = MyBatisMapperParser().parse("mapper/UserMapper.xml", content)

        assert mapper is not None, "the fixture must be recognised as a MyBatis mapper"
        assert mapper.namespace == "com.example.mapper.UserMapper"

        statement_ids = {statement.statement_id for statement in mapper.statements}
        assert statement_ids == {"findById", "findAll", "insertUser", "updateUser", "deleteById"}

    def test_sql_fragment_is_ignored(self) -> None:
        content = _MAPPER_XML.read_text(encoding="utf-8")
        mapper = MyBatisMapperParser().parse("mapper/UserMapper.xml", content)

        assert mapper is not None
        # <sql id="baseColumns"> is a reusable fragment, not an executable statement, and
        # must not appear among the bindings
        assert all(statement.statement_id != "baseColumns" for statement in mapper.statements)

    def test_statement_types_are_classified(self) -> None:
        content = _MAPPER_XML.read_text(encoding="utf-8")
        mapper = MyBatisMapperParser().parse("mapper/UserMapper.xml", content)

        assert mapper is not None
        type_by_id = {statement.statement_id: statement.statement_type for statement in mapper.statements}
        assert type_by_id["findById"] == "select"
        assert type_by_id["insertUser"] == "insert"
        assert type_by_id["updateUser"] == "update"
        assert type_by_id["deleteById"] == "delete"

    def test_line_numbers_are_zero_based_and_correct(self) -> None:
        content = _MAPPER_XML.read_text(encoding="utf-8")
        mapper = MyBatisMapperParser().parse("mapper/UserMapper.xml", content)

        assert mapper is not None
        find_by_id = mapper.find_statement("findById")
        assert find_by_id is not None
        # the <select id="findById"> opening tag is on line 9 (1-based) in the fixture,
        # i.e. line 8 when 0-based
        assert find_by_id.line == 8

    def test_qualified_id(self) -> None:
        content = _MAPPER_XML.read_text(encoding="utf-8")
        mapper = MyBatisMapperParser().parse("mapper/UserMapper.xml", content)

        assert mapper is not None
        statement = mapper.find_statement("findById")
        assert statement is not None
        assert statement.qualified_id(mapper.namespace) == "com.example.mapper.UserMapper.findById"

    def test_non_mapper_xml_returns_none(self) -> None:
        content = '<?xml version="1.0"?>\n<beans><bean id="x"/></beans>'
        assert MyBatisMapperParser().parse("beans.xml", content) is None

    def test_malformed_xml_returns_none(self) -> None:
        content = '<mapper namespace="com.example.Broken">\n  <select id="x">unclosed'
        assert MyBatisMapperParser().parse("broken.xml", content) is None

    def test_case_is_preserved_in_namespace(self) -> None:
        # an HTML parser would fold tag/attribute handling in ways that can corrupt
        # case-sensitive content; verify the namespace casing is preserved exactly
        content = '<mapper namespace="com.Example.MyMapper">\n  <select id="Get"/>\n</mapper>'
        mapper = MyBatisMapperParser().parse("m.xml", content)
        assert mapper is not None
        assert mapper.namespace == "com.Example.MyMapper"
        assert mapper.find_statement("Get") is not None


@pytest.mark.frameworks
class TestThymeleafTemplateParser:
    def test_variable_expressions_are_extracted(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        roots = template.root_variables()
        # ${user...}, ${pageTitle}, ${isAdmin}, ${items} are model attributes
        assert "user" in roots
        assert "pageTitle" in roots
        assert "isAdmin" in roots
        assert "items" in roots

    def test_th_object_root_is_extracted(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        # th:object="${userForm}" binds userForm as a model attribute
        assert "userForm" in template.root_variables()

    def test_selection_expressions_resolve_to_object_root(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        # *{email}, *{age}, *{name} all resolve against the enclosing th:object (userForm),
        # and must be reported as selection references to userForm
        selection_refs = [ref for ref in template.references if ref.is_selection]
        assert selection_refs, "selection expressions must be detected"
        assert all(ref.root_variable == "userForm" for ref in selection_refs)
        # there are three *{...} expressions in the fixture form
        assert len(selection_refs) == 3

    def test_th_each_local_variable_is_excluded(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        roots = template.root_variables()
        # "item" is the th:each loop variable (a local variable), NOT a model attribute,
        # and must be excluded; the iterated collection "items" must be included
        assert "item" not in roots
        assert "items" in roots

    def test_link_expressions_are_ignored(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        # @{/admin} is a link expression, not a model-attribute reference
        roots = template.root_variables()
        assert "admin" not in roots

    def test_variable_reference_line_numbers(self) -> None:
        content = _TEMPLATE_HTML.read_text(encoding="utf-8")
        template = ThymeleafTemplateParser().parse("templates/user_detail.html", content)

        # ${user.name} is on line 7 (1-based) in the fixture, i.e. line 6 (0-based)
        user_lines = sorted({ref.line for ref in template.references if ref.root_variable == "user"})
        assert 6 in user_lines

    def test_plain_html_yields_no_references(self) -> None:
        content = "<html><body><h1>Hello</h1></body></html>"
        template = ThymeleafTemplateParser().parse("plain.html", content)
        assert template.references == ()

    def test_is_candidate_detects_thymeleaf(self) -> None:
        parser = ThymeleafTemplateParser()
        assert parser.is_candidate('<p th:text="${x}">x</p>') is True
        assert parser.is_candidate('<p class="foo">x</p>') is False

    def test_th_each_loop_variable_excluded_in_descendant_elements(self) -> None:
        # a th:each loop variable is in scope for the whole subtree, so a reference on a
        # descendant element (the common <tr th:each> / child <td> pattern) must not be
        # reported as a model attribute; only the iterated collection is one
        content = (
            "<table>\n"
            '  <tr th:each="c : ${items}">\n'
            '    <td th:text="${c.id}">-</td>\n'
            '    <td th:text="${c.name}">-</td>\n'
            "  </tr>\n"
            "</table>\n"
        )
        roots = ThymeleafTemplateParser().parse("list.html", content).root_variables()
        assert "items" in roots
        assert "c" not in roots
