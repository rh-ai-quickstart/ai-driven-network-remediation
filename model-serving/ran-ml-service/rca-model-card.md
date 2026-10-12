---
license: apache-2.0
library_name: pytorch
tags: [time-series, telecomts, root-cause-analysis]
---

# Mantis-8M TelecomTS RCA head

The original pretrained RCA head from
`notebooks/telecomts_model_evaluation.ipynb` classifies anomalous 128-step,
18-channel TelecomTS KPI windows. It uses the `paris-noah/Mantis-8M` backbone,
which the serving image bakes in offline, plus the task checkpoint
`mantis_pretrained_rca.pt`. Training used seed 42 and 35 epochs. No retraining
is part of serving.

Input channel order is recorded in `rca_preprocessing.json`. Protocols map
TCP→0, UDP→1, and missing/None→2. For RCA, the TelecomTS reference computes
each channel's mean and population standard deviation across all RCA examples
and timesteps in a split. The service uses the fixed **training** split values
from `rca_preprocessing.json`; it never normalizes each request window. The
original notebook independently normalizes its test split, so the file also
records test statistics for exact notebook parity. The pretrained encoder
linearly interpolates 128 steps to 512 with `align_corners=False`.

Class indices, in order: 0 Antenna Failure; 1 Co-Channel Interference (Mild);
2 Co-Channel Interference (Severe); 3 Faulty RF Filters (Temporal);
4 Doppler Shift (Severe); 5 Faulty Handover Algorithm (Too Frequent);
6 Buffer Overflow (Gradual Buildup); 7 Resource Allocation Bugs;
8 High Network Congestion (Gradual Buildup); 9 High Network Congestion
(Sudden Spike). These match the TelecomTS `anomaly_type_2_id` mapping.
See the [TelecomTS reference preprocessing and class mapping](https://github.com/Ali-maatouk/TelecomTS/blob/main/src/utils/data_utils.py).

The notebook SHA-256 is
`7710e3fbd47e8a7237ac1cef1629b8c844d08b855012d837daef64de320c3cc4`.
The dataset revision is `1b3a88a440f4922edfcd1e230e42c003c0e13bdf`
with split seed 42. The backbone revision used by AD parity is
`bc7d5ab40c02133386a28e2c127f35c17c86901d`. The preprocessing file
SHA-256 is `628fe3226b6f5f059a0b0aba15cd12368723bee5d52348b7a7950b548457a62a`.
The RCA checkpoint revision, hash, notebook logits, and MLflow artifact link
must be recorded after the original export is recovered; the published AD
checkpoint cannot supply them.
