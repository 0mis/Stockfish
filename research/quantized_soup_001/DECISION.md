# Direct interpolation refused

Geometry passed: both original checkpoints have the exact192-route/92-head/1024-lane layout, the expected fallback map, every stored stack tag and exact EOF. Trained-coordinate identity failed every precommitted feature-plane and head gate.

| Feature plane | Retained sampled rows | Supported pairs | Median identity correlation | Assignment identity | Median identity minus best other |
|---|---:|---:|---:|---:|---:|
| FullThreats | 857 | 512/512 | 0.00649 | 1.95% | -0.35210 |
| PP_3Wide | 1024 | 512/512 | 0.00342 | 2.15% | -0.36490 |
| K32H3_block0 | 1024 | 512/512 | 0.01654 | 2.15% | -0.58798 |
| K32H3_block1 | 1024 | 512/512 | 0.04108 | 2.15% | -0.60432 |
| K32H3_block2 | 1024 | 512/512 | 0.03531 | 1.95% | -0.59690 |

Aggregate median identity correlation was 0.01859; only 2.15% of supported pairs remained identity in the best permissible diagnostic assignment. That assignment improved mean correlation over direct identity by 0.46464; it was never applied. Required identity matching was90%, median identity correlation0.95. All512 pairs were supported, so this is not a missing-support refusal.

| Layer | Informative heads | Median centered weight correlation | Maximum | Passing heads |
|---|---:|---:|---:|---:|
| FC0 | 92/92 | 0.07802 | 0.24383 | 0/92 |
| FC1 | 92/92 | 0.30139 | 0.59475 | 0/92 |
| FC2 | 92/92 | 0.69184 | 0.84928 | 0/92 |

Every FC0/FC1 head had32 informative row comparisons. Common zeros, padding and constant vectors were excluded. All92 FC2 vectors were informative. The required layer correlation was0.90, with additional hidden-row support and0.75 coverage gates.

Decision: **STOP_DIRECT_INTERPOLATION_LINE**. No3:1 candidate, permutation, fitting, native qualification or games were produced. This result concerns direct weight-coordinate compatibility; it does not measure chess strength or establish official superiority.

The plan was committed before real measurement. The first launch was rejected for a YAML command-scalar syntax error and ran no jobs; the recovery changed only that workflow block. The complete recovered YAML passed parser validation. Ten synthetic tests passed on standard free GitHub Actions. The measured alignment phase took57.03 seconds including the two public downloads.

[Measured CI run](https://github.com/0mis/Stockfish/actions/runs/42694359294) · [Frozen method commit](https://github.com/0mis/Stockfish/commit/6e29caa7b74b9600a42dfcbeb5910190381229b6) · [Measured source commit](https://github.com/0mis/Stockfish/commit/096e58412b893c1bc9530a28b95ab67a9b289ce9)

The original checkpoints/source are credited to Shawn Xu and the Stockfish contributors. Wortsman et al. and Ainsworth et al. supply motivation only. The ongoing test1758 nine-bias experiment and follow-up scores were not read or changed. All local staging stayed in external TEMP; real-weight computation occurred only in standard public CI.
