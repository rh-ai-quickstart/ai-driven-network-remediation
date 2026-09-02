"""Unit tests for demo.py's fixture catalog trigger logic (no Kafka)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from ran_chatbot_service.demo import build_demo_sample, publish_demo_metrics


class TestBuildDemoSample:
    def test_antenna_failure_returns_valid_json_and_meta(self):
        json_blob, meta = build_demo_sample("antenna_failure")

        assert meta["scenario"] == "antenna_failure"
        assert meta["incident_id"]
        assert meta["zone"]
        assert meta["application"]

        payload = json.loads(json_blob)
        assert "incident_id" in payload
        assert "zone" in payload
        assert "application" in payload
        assert "kpi_window" in payload
        assert len(payload["kpi_window"]) == 128

    def test_normal_traffic_scenario(self):
        json_blob, meta = build_demo_sample("normal_traffic")

        assert meta["scenario"] == "normal_traffic"
        payload = json.loads(json_blob)
        assert len(payload["kpi_window"]) == 128

    def test_high_congestion_scenario(self):
        json_blob, meta = build_demo_sample("high_congestion_sudden")

        assert meta["scenario"] == "high_congestion_sudden"
        payload = json.loads(json_blob)
        assert len(payload["kpi_window"]) == 128

    def test_unknown_scenario_falls_back_to_default(self):
        json_blob, meta = build_demo_sample("not-a-real-scenario")

        assert meta["scenario"] == "antenna_failure"
        payload = json.loads(json_blob)
        assert len(payload["kpi_window"]) == 128

    def test_case_and_whitespace_insensitive(self):
        _, meta = build_demo_sample("  Antenna_Failure  ")
        assert meta["scenario"] == "antenna_failure"

    def test_kpi_window_has_18_channels_per_timestep(self):
        json_blob, _ = build_demo_sample("antenna_failure")
        payload = json.loads(json_blob)
        timestep = payload["kpi_window"][0]

        expected_channels = {
            "RSRP", "DL_BLER", "DL_MCS", "UL_BLER", "UL_MCS", "UL_NPRB",
            "UL_SNR", "TX_Bytes", "RX_Bytes", "Estimated_UL_Buffer",
            "PRBs_DL_Current", "PRBs_UL_Current", "PRB_Utilization_DL",
            "PRB_Utilization_UL", "UL_Protocol", "UL_NumberOfPackets",
            "DL_Protocol", "DL_NumberOfPackets",
        }
        assert expected_channels.issubset(set(timestep.keys()))

    def test_each_call_generates_unique_incident_id(self):
        _, meta1 = build_demo_sample("antenna_failure")
        _, meta2 = build_demo_sample("antenna_failure")
        assert meta1["incident_id"] != meta2["incident_id"]

    def test_meta_does_not_contain_cell_id_or_band(self):
        _, meta = build_demo_sample("antenna_failure")
        assert "cell_id" not in meta
        assert "band" not in meta


class TestPublishDemoMetrics:
    @patch("kafka.KafkaProducer")
    def test_closes_producer_and_returns_offset_on_success(self, mock_producer_cls):
        mock_producer = MagicMock()
        mock_producer.send.return_value.get.return_value = MagicMock(offset=42)
        mock_producer_cls.return_value = mock_producer

        offset = publish_demo_metrics('{"test": true}')

        assert offset == 42
        mock_producer.close.assert_called_once_with(timeout=10)

    @patch("kafka.KafkaProducer")
    def test_closes_producer_even_if_future_get_raises(self, mock_producer_cls):
        mock_producer = MagicMock()
        mock_producer.send.return_value.get.side_effect = Exception("Kafka unreachable")
        mock_producer_cls.return_value = mock_producer

        with pytest.raises(Exception, match="Kafka unreachable"):
            publish_demo_metrics('{"test": true}')

        mock_producer.close.assert_called_once_with(timeout=10)
