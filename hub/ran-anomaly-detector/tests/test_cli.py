from click.testing import CliRunner
from ran_anomaly_detector import main


def test_cli_runs_with_default_sample_and_reports_anomalies():
    runner = CliRunner()
    result = runner.invoke(main, [])

    assert result.exit_code == 0
    assert "LowRsrp" in result.output
    assert "ThroughputDrop" in result.output
    assert "UesSpikeOrDrop" in result.output


def test_cli_with_custom_file_and_no_anomalies(tmp_path):
    csv_path = tmp_path / "healthy.csv"
    csv_path.write_text(
        "cell_id,max_capacity,lat,lon,area_type,city,band,frequency,datetime,"
        "ues_usage,rsrp,rsrq,sinr,throughput_mbps,latency_ms\n"
        "1,100,33.0,-97.0,industrial,Plano,Band 29,700,2026-07-29T10:00:00Z,50,"
        "-95.0,-10.0,15.0,100.0,20.0\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--file", str(csv_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "No anomalies detected."


def test_cli_rejects_nonexistent_file():
    runner = CliRunner()
    result = runner.invoke(main, ["--file", "/nonexistent/path.csv"])

    assert result.exit_code != 0
