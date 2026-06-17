"""
Tests for the framework binding index.

These tests exercise the project scan and the bidirectional lookup tables of
:class:`~serena.frameworks.binding_index.FrameworkBindingIndex`. They build a real
:class:`~serena.project.Project` over the framework fixture repository but do not require a
language server, since the index's project scan only reads files and consults the ignore
spec.

The fixture repository is copied to a temporary location first, so that loading the project
(which writes a ``.serena`` folder) does not modify the checked-in fixture.
"""

import shutil
from pathlib import Path

import pytest

from serena.frameworks.binding_index import FrameworkBindingIndex
from serena.project import Project
from test.conftest import create_default_serena_config

_FIXTURE_REPO = Path(__file__).parent.parent.parent / "resources" / "repos" / "frameworks" / "test_repo"


@pytest.fixture()
def framework_project(tmp_path: Path) -> Project:
    # copy the fixture to a temporary location so that project loading (which creates a
    # .serena folder) does not pollute the checked-in fixture
    repo_copy = tmp_path / "test_repo"
    shutil.copytree(_FIXTURE_REPO, repo_copy)
    return Project.load(str(repo_copy), serena_config=create_default_serena_config())


@pytest.mark.frameworks
class TestFrameworkBindingIndexMyBatis:
    def test_scan_discovers_mapper(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        namespaces = {mapper.namespace for mapper in index.iter_mapper_files()}
        assert "com.example.mapper.UserMapper" in namespaces

    def test_java_method_to_xml_statement(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)

        # the Java -> XML direction: the fully qualified method id resolves to the bound
        # statement's location
        location = index.find_statement_for_qualified_method("com.example.mapper.UserMapper.findById")
        assert location is not None
        assert location.statement.statement_id == "findById"
        assert location.statement.statement_type == "select"
        assert location.relative_path.replace("\\", "/").endswith("mapper/UserMapper.xml")
        assert location.line == 8

    def test_all_methods_resolve(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        for method, expected_type in [
            ("findById", "select"),
            ("findAll", "select"),
            ("insertUser", "insert"),
            ("updateUser", "update"),
            ("deleteById", "delete"),
        ]:
            location = index.find_statement_for_qualified_method(f"com.example.mapper.UserMapper.{method}")
            assert location is not None, f"method {method} should resolve to a statement"
            assert location.statement.statement_type == expected_type

    def test_unknown_method_returns_none(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        assert index.find_statement_for_qualified_method("com.example.mapper.UserMapper.doesNotExist") is None

    def test_namespace_lookup(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        mapper = index.find_namespace_mapper("com.example.mapper.UserMapper")
        assert mapper is not None
        # the XML -> Java direction starts here: statement id -> bound method name
        statement = mapper.find_statement("insertUser")
        assert statement is not None
        assert statement.qualified_id(mapper.namespace) == "com.example.mapper.UserMapper.insertUser"


@pytest.mark.frameworks
class TestFrameworkBindingIndexThymeleaf:
    def test_scan_discovers_template(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        paths = {template.relative_path.replace("\\", "/") for template in index.iter_template_files()}
        assert any(path.endswith("templates/user_detail.html") for path in paths)

    def test_model_attribute_to_template_bindings(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)

        # the Java -> HTML direction: the model attribute "user" resolves to the template
        # locations that reference it
        bindings = index.find_template_bindings_for_variable("user")
        assert bindings, "the 'user' model attribute must have template references"
        assert all(binding.relative_path.replace("\\", "/").endswith("templates/user_detail.html") for binding in bindings)
        # ${user.name} and ${user.email} are on two distinct lines
        referenced_lines = {binding.line for binding in bindings}
        assert len(referenced_lines) >= 2

    def test_selection_object_attribute_resolves(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        # userForm is the th:object root and must be discoverable as a model attribute
        bindings = index.find_template_bindings_for_variable("userForm")
        assert bindings
        assert any(binding.reference.is_selection for binding in bindings)

    def test_each_local_variable_is_not_a_model_attribute(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        # "item" is a th:each loop variable; it must not be indexed as a model attribute
        assert index.find_template_bindings_for_variable("item") == []
        # while the iterated collection "items" must be
        assert index.find_template_bindings_for_variable("items")

    def test_unknown_attribute_returns_empty(self, framework_project: Project) -> None:
        index = FrameworkBindingIndex.build(framework_project)
        assert index.find_template_bindings_for_variable("nonexistentAttribute") == []
