from ran_anomaly_detector.detection import AnomalyDetectionService

HEADER = (
    "cell_id,max_capacity,lat,lon,area_type,city,band,frequency,datetime,"
    "ues_usage,rsrp,rsrq,sinr,throughput_mbps,latency_ms"
)


def _row(
    cell_id=42,
    max_capacity=100,
    band="Band 29",
    minute=0,
    ues_usage=50,
    rsrp=-95.0,
    rsrq=-10.0,
    sinr=15.0,
    throughput_mbps=100.0,
    latency_ms=20.0,
) -> str:
    return (
        f"{cell_id},{max_capacity},33.05,-96.8,industrial,Plano,{band},700,"
        f"2026-07-29T10:{minute:02d}:00Z,{ues_usage},{rsrp},{rsrq},{sinr},{throughput_mbps},{latency_ms}"
    )


def test_low_rsrp_anomaly_matches_spec_output_shape():
    csv_blob = "\n".join([HEADER, _row(rsrp=-125.0)])

    service = AnomalyDetectionService()
    outputs = service.process_csv(csv_blob)

    assert outputs == [
        {
            "cell_id": 42,
            "band": "Band 29",
            "anomaly_type": "LowRsrp",
            "anomaly": "Low RSRP: -125.0 dBm < -110.0 dBm",
        }
    ]


def test_throughput_drop_anomaly_matches_spec_output_shape():
    csv_blob = "\n".join(
        [
            HEADER,
            _row(minute=0, throughput_mbps=50.00),
            _row(minute=5, throughput_mbps=54.00),
            _row(minute=10, throughput_mbps=60.25),
            _row(minute=15, throughput_mbps=18.89),
        ]
    )

    service = AnomalyDetectionService()
    outputs = service.process_csv(csv_blob)

    assert {
        "cell_id": 42,
        "band": "Band 29",
        "anomaly_type": "ThroughputDrop",
        "anomaly": "Throughput Drop: 18.89 Mbps (Current) vs. 54.75 Mbps (Avg Prior) - drop > 50%",
    } in outputs


def test_cell_outage_anomaly_matches_spec_output_shape():
    csv_blob = "\n".join(
        [
            HEADER,
            _row(ues_usage=0, throughput_mbps=0.0, sinr=-10.0, rsrp=-120.0, rsrq=-20.0),
        ]
    )

    service = AnomalyDetectionService()
    outputs = service.process_csv(csv_blob)

    assert {
        "cell_id": 42,
        "band": "Band 29",
        "anomaly_type": "CellOutage",
        "anomaly": "Cell Outage: UEs=0, Tput=0, SINR=-10.0, RSRP=-120.0, RSRQ=-20.0",
    } in outputs


def test_no_anomalies_for_healthy_readings():
    csv_blob = "\n".join([HEADER, _row()])

    service = AnomalyDetectionService()
    outputs = service.process_csv(csv_blob)

    assert outputs == []


def test_history_persists_across_separate_process_message_calls():
    """A single Kafka message only carries a slice of the stream — history must
    accumulate across calls, not just within one CSV blob."""
    service = AnomalyDetectionService()

    for minute, throughput in [(0, 50.00), (5, 54.00), (10, 60.25)]:
        outputs = service.process_message(
            ("\n".join([HEADER, _row(minute=minute, throughput_mbps=throughput)])).encode("utf-8")
        )
        assert outputs == []

    outputs = service.process_message(("\n".join([HEADER, _row(minute=15, throughput_mbps=18.89)])).encode("utf-8"))

    assert any(o["anomaly_type"] == "ThroughputDrop" for o in outputs)


def test_empty_message_returns_no_anomalies():
    service = AnomalyDetectionService()

    assert service.process_message(b"") == []
    assert service.process_message(b"   ") == []
