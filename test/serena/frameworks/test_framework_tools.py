"""
End-to-end tests for the framework navigation tools.

These tests drive the four optional framework tools through a real
:class:`~serena.agent.SerenaAgent` over the framework fixture repository and assert the
concrete contents of their results.

The fixture project is configured with *no* languages, so that no language server is
started: the framework tools resolve their bindings directly from the project's files and
do not depend on a language server. This keeps the tests fast and free of language-server
dependencies in CI, while still exercising the full tool -> index -> parser path.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from serena.agent import SerenaAgent
from serena.config.serena_config import ProjectConfig, SerenaConfig
from serena.tools.framework_tools import (
    FindMapperMethodForXmlTool,
    FindMapperXmlForMethodTool,
    FindThymeleafBindingsForModelAttributeTool,
    ListThymeleafModelAttributesTool,
)

_FIXTURE_REPO = Path(__file__).parent.parent.parent / "resources" / "repos" / "frameworks" / "test_repo"

_MAPPER_INTERFACE = "src/main/java/com/example/mapper/UserMapper.java"
_MAPPER_XML = "src/main/resources/mapper/UserMapper.xml"
_TEMPLATE_HTML = "src/main/resources/templates/user_detail.html"


@pytest.fixture()
def framework_agent(tmp_path: Path) -> Iterator[SerenaAgent]:
    # copy the fixture so that project/config writes do not modify the checked-in fixture
    repo_copy = tmp_path / "test_repo"
    shutil.copytree(_FIXTURE_REPO, repo_copy)

    serena_config = SerenaConfig(gui_log_window=False, web_dashboard=False)
    # configure the project with no languages so that no language server is started; the
    # framework tools do not require one
    ProjectConfig.autogenerate(repo_copy, serena_config=serena_config, project_name="fw_test", languages=[], save_to_disk=True)

    agent = SerenaAgent(project=str(repo_copy), serena_config=serena_config)
    agent.execute_task(lambda: None)
    yield agent
    agent.on_shutdown(timeout=5)


@pytest.mark.frameworks
class TestFindMapperXmlForMethodTool:
    def test_resolves_method_to_statement(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindMapperXmlForMethodTool)
        result = json.loads(tool.apply(relative_path=_MAPPER_INTERFACE, method_name="findById"))

        assert result["method"] == "com.example.mapper.UserMapper.findById"
        assert result["statement_type"] == "select"
        assert result["relative_path"].replace("\\", "/").endswith("mapper/UserMapper.xml")
        assert result["line"] == 8

    def test_unknown_method_reports_no_binding(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindMapperXmlForMethodTool)
        result = tool.apply(relative_path=_MAPPER_INTERFACE, method_name="doesNotExist")
        assert "No MyBatis mapper statement found" in result


@pytest.mark.frameworks
class TestFindMapperMethodForXmlTool:
    def test_resolves_statement_to_method(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindMapperMethodForXmlTool)
        result = json.loads(tool.apply(relative_path=_MAPPER_XML, statement_id="updateUser"))

        assert result["mapper_namespace"] == "com.example.mapper.UserMapper"
        assert result["statement_id"] == "updateUser"
        assert result["statement_type"] == "update"
        assert result["java_method_id"] == "com.example.mapper.UserMapper.updateUser"
        assert result["name_path_in_interface"] == "UserMapper/updateUser"

    def test_unknown_statement_lists_available(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindMapperMethodForXmlTool)
        result = tool.apply(relative_path=_MAPPER_XML, statement_id="nope")
        assert "declares no statement with id='nope'" in result
        # the message should enumerate the real statement ids to aid correction
        assert "findById" in result


@pytest.mark.frameworks
class TestFindThymeleafBindingsForModelAttributeTool:
    def test_resolves_attribute_to_template_locations(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindThymeleafBindingsForModelAttributeTool)
        result = json.loads(tool.apply(model_attribute="user"))

        assert result["model_attribute"] == "user"
        assert result["reference_count"] == 2
        lines = sorted(reference["line"] for reference in result["references"])
        assert lines == [6, 7]
        assert all(
            reference["relative_path"].replace("\\", "/").endswith("templates/user_detail.html") for reference in result["references"]
        )

    def test_selection_attribute_marked_as_selection(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindThymeleafBindingsForModelAttributeTool)
        result = json.loads(tool.apply(model_attribute="userForm"))
        assert any(reference["is_selection_expression"] for reference in result["references"])

    def test_each_local_variable_has_no_binding(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(FindThymeleafBindingsForModelAttributeTool)
        # "item" is a th:each loop variable, not a model attribute
        result = tool.apply(model_attribute="item")
        assert "No Thymeleaf template references found" in result


@pytest.mark.frameworks
class TestListThymeleafModelAttributesTool:
    def test_lists_all_model_attributes(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(ListThymeleafModelAttributesTool)
        result = json.loads(tool.apply(relative_path=_TEMPLATE_HTML))

        attribute_names = {attribute["name"] for attribute in result["model_attributes"]}
        assert attribute_names == {"user", "userForm", "items", "pageTitle", "isAdmin"}
        # "item" (th:each loop variable) must not be listed as a model attribute
        assert "item" not in attribute_names

    def test_reports_reference_lines(self, framework_agent: SerenaAgent) -> None:
        tool = framework_agent.get_tool(ListThymeleafModelAttributesTool)
        result = json.loads(tool.apply(relative_path=_TEMPLATE_HTML))

        lines_by_name = {attribute["name"]: attribute["referenced_on_lines"] for attribute in result["model_attributes"]}
        # ${user.name} and ${user.email} are referenced on two distinct lines
        assert lines_by_name["user"] == [6, 7]
