"""Ordered TelecomTS root-cause class labels shared between the classify
model artifact and the RCA graph.

The 10 labels correspond to the synthetic anomaly types in the TelecomTS
benchmark dataset (Jamming excluded — it's a real anomaly type treated
separately). The order matches the model's output class indices 0–9.
"""

RCA_CLASSES: tuple[str, ...] = (
    "Antenna Failure",
    "Co-Channel Interference (Mild)",
    "Co-Channel Interference (Severe)",
    "Faulty RF Filters (Temporal)",
    "Doppler Shift (Severe)",
    "Faulty Handover Algorithm (Too Frequent)",
    "Buffer Overflow (Gradual Buildup)",
    "Resource Allocation Bugs",
    "High Network Congestion (Gradual Buildup)",
    "High Network Congestion (Sudden Spike)",
)
