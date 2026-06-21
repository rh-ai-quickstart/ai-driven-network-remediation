"""Integration tests for Kafka MCP tools via the deployed mcp-noc-kafka server.

Requires a deployed kafka-mcp instance.
"""

import uuid

import pytest
from conftest import mcp_call, mcp_list_tools

pytestmark = pytest.mark.flaky(reruns=5)

# Must be in both KAFKA_CONSUME_TOPICS and KAFKA_PRODUCE_TOPICS allowlists
TEST_TOPIC = "remediation-jobs"

EXPECTED_TOOLS = {
    "list_topics",
    "consume_topic",
    "produce_message",
    "get_consumer_lag",
}


def test_kafka_tools_list(mcp_kafka_client):
    """Verify the MCP tools/list endpoint returns all expected Kafka tools."""
    tool_names = mcp_list_tools(mcp_kafka_client)
    assert EXPECTED_TOOLS.issubset(tool_names), f"Missing tools: {EXPECTED_TOOLS - tool_names}"


@pytest.fixture(scope="module")
def seeded_topic(mcp_kafka_client):
    """Seed an allowed topic with a message so downstream tests have data."""
    marker = uuid.uuid4().hex[:8]
    result = mcp_call(
        mcp_kafka_client,
        "produce_message",
        {"topic": TEST_TOPIC, "message": {"_seed": True, "_marker": marker}},
    )
    if not result.get("success"):
        pytest.skip(f"Cannot seed topic: {result.get('message', '')}")
    yield TEST_TOPIC


class TestListTopics:
    def test_returns_topics(self, mcp_kafka_client, seeded_topic):
        result = mcp_call(mcp_kafka_client, "list_topics")
        assert result["success"] is True
        assert isinstance(result["topics"], list)
        assert result["count"] > 0
        topic_names = [t["name"] for t in result["topics"]]
        assert seeded_topic in topic_names


class TestProduceConsumeRoundTrip:
    def test_round_trip(self, mcp_kafka_client, seeded_topic):
        # Produce a message then immediately consume from the same topic,
        # verifying the message survives the full MCP → Kafka → MCP path.
        test_id = f"integration-{uuid.uuid4().hex[:8]}"
        produce_result = mcp_call(
            mcp_kafka_client,
            "produce_message",
            {
                "topic": seeded_topic,
                "message": {"test_id": test_id, "data": "hello"},
            },
        )
        assert produce_result["success"] is True
        assert produce_result["topic"] == seeded_topic

        consume_result = mcp_call(
            mcp_kafka_client,
            "consume_topic",
            {
                "topic": seeded_topic,
                "max_messages": 10,
                "timeout_ms": 10000,
            },
        )
        assert consume_result["success"] is True
        assert consume_result["count"] >= 1

        values = [m["value"] for m in consume_result["messages"]]
        assert any(v.get("test_id") == test_id for v in values if isinstance(v, dict))


class TestGetConsumerLag:
    def test_returns_structured_response(self, mcp_kafka_client, seeded_topic):
        # Fresh consumer group has no committed offsets, so lag equals
        # the total number of messages in the topic.
        group_id = f"test-group-{uuid.uuid4().hex[:8]}"
        result = mcp_call(
            mcp_kafka_client,
            "get_consumer_lag",
            {"group_id": group_id, "topic": seeded_topic},
        )
        assert result["success"] is True, f"Tool error: {result}"
        assert result["group_id"] == group_id
        assert result["topic"] == seeded_topic
        assert result["total_lag"] > 0
        assert result["status"] == "healthy"
        assert isinstance(result["partitions"], list)
        assert len(result["partitions"]) >= 1
        partition = result["partitions"][0]
        assert partition["lag"] > 0
        assert partition["committed_offset"] == 0
        assert partition["end_offset"] > 0
