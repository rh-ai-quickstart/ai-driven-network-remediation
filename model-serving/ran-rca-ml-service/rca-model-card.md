---
license: apache-2.0
library_name: pytorch
tags: [time-series, telecomts, root-cause-analysis]
---

# Mantis-8M TelecomTS RCA head

The newly trained RCA head based on
`../shared-ml-predictor/notebooks/telecomts_model_evaluation.ipynb` classifies anomalous 128-step,
18-channel TelecomTS KPI windows. It uses the `paris-noah/Mantis-8M` backbone,
which the serving image bakes in offline, plus the task checkpoint
`mantis_pretrained_rca.pt`. Recovery used seed 42 and 35 epochs. No retraining
is part of serving.

Input channel order and the ordered RCA class mapping are recorded in
`rca_preprocessing.json`. Protocols map
TCP→0, UDP→1, and missing/None/empty→2. For RCA, the TelecomTS reference computes
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
SHA-256 is `1a8e0144bb4a65f48133c4ad36491aba6432b817d64b8d3521a3c8de913ddeae`.

## Recovery result (issue #196)

The new 35-epoch checkpoint was evaluated through the existing predictor on
the original 80/20 seed-42 split. On all 192 held-out 10-class RCA examples,
training-split normalization gave **92.71% accuracy** and **0.9274 macro F1**,
passing both 0.90 gates. The notebook-style test-split normalization comparison
gave 92.19% accuracy and 0.9086 macro F1. These are scores for the new
checkpoint, not the unavailable historical weights. The 58 Jamming examples
were reported separately as unsupported inputs, with no accuracy claim. Their
highest ten-class confidence was **0.8364**; several therefore exceed the
initial 0.6 guidance threshold. The classifier has no Jamming or unknown
class, and confidence alone does not detect unsupported inputs. Only forward
windows from the supported ten-class population to this classifier. If an
upstream source can identify Jamming, exclude those windows before
classification. The current binary anomaly detector does not distinguish
Jamming, so that integration retains this risk. Raising the threshold alone is
not a reliable out-of-domain gate. Full
per-class precision, recall, F1, support, confusion matrix, and
provenance are in [the validation report](../../docs/model-runs/issue-196-rca-validation.json).

Checkpoint SHA-256: `0ecd02d50eed50130476c0c543503f9f3d1595507502664d3e5b8fb01c3eccfe`.
Preprocessing SHA-256: `1a8e0144bb4a65f48133c4ad36491aba6432b817d64b8d3521a3c8de913ddeae`.
Both files are retained privately at
`~/Library/Application Support/ai-driven-network-remediation/issue-196/`,
independent of the training directory and repository workspace. A workspace
copy at `model-serving/ran-rca-ml-service/.private-artifacts/issue-196/` is ignored
by Git and excluded from image builds. Both copies have matching hashes; the
persistent copy was reloaded through the classifier.
Synthetic TelecomTS results alone do not establish production generalization.

## Pinned serving reference coverage

`tests/fixtures/rca_reference.json` records local `/v1/classify` outputs and
ten logits for four TelecomTS fixtures using the recovered checkpoint. It also
pins each fixture's SHA-256, the notebook hash, the dataset revision and split
seed, the ordered ten-class mapping, and both artifact hashes. The dedicated
model-serving parity CI pins `RCA_ARTIFACT_COMMIT` to the published repository
commit and compares class index and label exactly, with absolute tolerances of
0.0001 for confidence and logits. These are local serving
observations, not outputs from the unavailable historical notebook checkpoint.

| Fixture | Class index | Confidence |
| --- | ---: | ---: |
| Antenna failure | 0 | 0.9293 |
| Severe co-channel interference | 2 | 0.9502 |
| Severe Doppler shift | 4 | 0.9568 |
| Sudden high congestion | 9 | 0.8996 |
