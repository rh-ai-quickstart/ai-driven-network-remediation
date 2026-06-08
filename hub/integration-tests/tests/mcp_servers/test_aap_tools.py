"""Integration tests verifying noc-aap exposes and executes its MCP tools."""

from conftest import mcp_call, mcp_list_tools

EXPECTED_TOOLS = {
    "list_job_templates",
    "launch_job",
    "upsert_job_template",
    "get_job_status",
    "get_job_output",
}


def test_aap_tools_list(mcp_aap_client):
    """Verify the MCP tools/list endpoint returns all expected AAP tools."""
    tool_names = mcp_list_tools(mcp_aap_client)
    assert EXPECTED_TOOLS.issubset(tool_names), f"Missing tools: {EXPECTED_TOOLS - tool_names}"


def test_list_job_templates(mcp_aap_client):
    """Call list_job_templates and verify seed templates are returned."""
    result = mcp_call(mcp_aap_client, "list_job_templates")
    assert result["success"] is True
    assert result["count"] >= 2
    names = [t["name"] for t in result["job_templates"]]
    assert "restart-nginx" in names


def test_launch_job(mcp_aap_client):
    """Launch the seed template and verify a job_id is returned."""
    result = mcp_call(
        mcp_aap_client,
        "launch_job",
        {"job_template_name": "restart-nginx"},
    )
    assert result["success"] is True
    assert "job_id" in result
    assert result["template_name"] == "restart-nginx"


def test_get_job_status(mcp_aap_client):
    """Launch a job then check its status."""
    launch = mcp_call(
        mcp_aap_client,
        "launch_job",
        {"job_template_name": "restart-nginx"},
    )
    job_id = launch["job_id"]

    result = mcp_call(mcp_aap_client, "get_job_status", {"job_id": job_id})
    assert result["success"] is True
    assert result["job_id"] == job_id
    assert result["status"] == "successful"
    assert result["failed"] is False


def test_get_job_output(mcp_aap_client):
    """Launch a job then retrieve its stdout."""
    launch = mcp_call(
        mcp_aap_client,
        "launch_job",
        {"job_template_name": "restart-nginx"},
    )
    job_id = launch["job_id"]

    result = mcp_call(
        mcp_aap_client,
        "get_job_output",
        {"job_id": job_id, "last_lines": 50},
    )
    assert result["success"] is True
    assert result["job_id"] == job_id
    assert "PLAY" in result["output"]
    assert result["total_lines"] >= 1


def test_upsert_job_template(mcp_aap_client):
    """Create a new template via upsert (copy from seed) and verify."""
    result = mcp_call(
        mcp_aap_client,
        "upsert_job_template",
        {
            "template_name": "ci-test-template",
            "playbook": "ci-test.yml",
            "base_template_name": "restart-nginx",
        },
    )
    assert result["success"] is True
    assert result["created"] is True
    assert result["playbook"] == "ci-test.yml"

    templates = mcp_call(mcp_aap_client, "list_job_templates")
    names = [t["name"] for t in templates["job_templates"]]
    assert "ci-test-template" in names
