# Colab training runs

## Latest run: Training Camp generalist

Run ID: `clashai_training_camp_generalist_fv3_colab_20261120T183911Z` (21 November 2026, Asia/Bangkok). The notebook trained a fresh ClashAI GenModel FV3 generalist for 20 epochs on a Tesla T4 with BF16; the trainer selected epoch 12 by `cell_tile_top1`.

- Drive folder: `MyDrive/OpenCRBOT/runs/clashai_training_camp_generalist_fv3_colab_20261120T183911Z/`
- Local run folder: `runs/clashai-replay-data/training-camp-generalist-20261121/colab-run-20261120T183911Z/`
- Checkpoint: `gen_clashai_training_camp_generalist_fv3_colab_20261120T183911Z_s0.pt`
- Checkpoint SHA-256: `8fae9a846b1ab57432230c436ffa7a9e7ad17ce9e7a4185cfe92d48f4c689a95`
- Checkpoint size: 5,565,717 bytes; weights and metadata only (no optimizer/scheduler state)
- Dataset: 22,007 rows; 20,373 train and 1,634 held-out; 95 decks and 114 card identities
- Runtime: Tesla T4, BF16; 20 epochs took 556 seconds; selected epoch 12
- Configuration: seed 0, batch size 256, lattice grid, `cell_tile_top1` selection
- Held-out plays: 481 / 1,634 rows; `cell_tile_top1=0.0956`, `card_top1=0.3306`, `joint_top1=0.0249`, `place_dist=7.019`, `value_acc=0.4486`
- Baselines: per-card tile accuracy 0.0769; hand-frequency card accuracy 0.3222
- `v3val` contains 0 rows; use the `val_all` held-out figures above, not the empty slice
- Artifacts: `colab_run_manifest.json`, `heldout_eval.json`, `hist_gen_*.json`, `final_gen_*.json`, and `cp3-clashai-compare.json`

All eight cards in the recorded GPGPC Training Camp deck are present in this checkpoint's vocabulary, and OpenCRBOT's FV3 adapter loads it with `configs/training-camp-deck.yaml`. On the 13-frame CP3 analysis, five frames were skipped because the match was inactive or tower state was unknown. The eight compared decisions were all `wait` (five `wait`, three `no_affordable`); the baseline suggested plays on three of those frames. The offline policy agreement was 5/8. This confirms model/deck compatibility and inference only; it does not pass CP3 or authorize live recommendations.

The training source is weak for a real-game policy. It contains no exact GPGPC eight-card deck. A balanced 120-tag selection found 68 Archers-containing replay reconstructions; 4,431 of 5,484 source card plays were accepted by RoyaleSim, 40/68 replays ended before all source plays were reconstructed, 293 ability events were omitted, and 35/68 simulated crown results differed from the source. The combined dataset also includes the earlier 25-match IceBow corpus. Some rare target cards have little held-out support; Musketeer has no held-out plays. Treat this checkpoint as offline research only.

The notebook and inputs were uploaded to `MyDrive/OpenCRBOT/`; the notebook verified the dataset and code bundle hashes and required a GPU. The Drive manifest is the source of record for code/data/checkpoint hashes and the full evaluation. The checkpoint downloaded into the local run folder has the same SHA-256 as the manifest.

## Previous run: IceBow generalist

The earlier ClashAI IceBow FV3 run completed from scratch on Google Colab with a Tesla T4. It trained 20 epochs and selected epoch 12 by `cell_tile_top1`.

- Run ID: `clashai_icebow_fv3_colab_20261120T172616Z`
- Drive folder: `MyDrive/OpenCRBOT/runs/clashai_icebow_fv3_colab_20261120T172616Z/`
- Local checkpoint: `runs/clashai-replay-data/colab-checkpoint-20261121/gen_clashai_icebow_fv3_colab_20261120T172616Z_s0.pt`
- SHA-256: `cd86c8b284b61420801eb37f29e2bab07977de504ea285b18881ef7a183aa3ce`
- Size: 5,556,125 bytes; held-out: 105 play / 308 rows
- Metrics: `cell_tile_top1=0.1333`, `card_top1=0.3810`, `joint_top1=0.0476`, `place_dist=7.105`, `value_acc=0.9481`

This checkpoint does not contain `archers`, so the adapter correctly rejects it for the GPGPC Training Camp deck. Its source is also reconstructed simulator state, not original game observations; it is not approved for live play.

## Reproducible inputs

- Training Camp notebook: `training_camp_generalist_fv3_train.ipynb`, SHA-256 `bd3c73684b9368ed6d463ce56ab90716d8daa86e07294723472ccb0553c584a3`
- Previous IceBow notebook: `icebow_fv3_train.ipynb`
- Code bundle: `clashai_pipeline_bundle.zip`, SHA-256 `0c768918cca15be966bbf6b902be113a8bccaaba8be6c93278e5d99c83dc9a5e`
- The code bundle archive is kept outside Git; use the SHA-256-matching copy from Google Drive.
- Training Camp dataset: `../runs/clashai-replay-data/training-camp-generalist-20261121/dataset-training-camp-generalist-fv3.npz`, SHA-256 `c4c59f52ede3ff7043cc46147ddf6fdab2104485e565d0ab2417c5bfdf4b27fd`
- Training Camp source selection: SHA-256 `8e356c7a2f7a9549e8c9ca3b61bcfd22196cd968c81e610b3d1aecd06c69467a`
- Training Camp replay manifest: SHA-256 `a320e777abb76af9618a7fd56aa27fb1b7cef802bc68e8d3054301b6b0253eba`
- IceBow dataset: `../runs/clashai-replay-data/dataset-icebow-fv3-source-result.npz`, SHA-256 `1a36de577ca5ef2d6980de7d0e0d892460be5bee07b0a9f965eba952c0fc7422`
- IceBow source corpus manifest: SHA-256 `818ba57895228e30674d41c1c13c45edb285ff6c203d642669dab7cd2a55af98`

The Training Camp run started from scratch; the interrupted CPU pilot was not used because optimizer state was not saved. Each subsequent run should use a new timestamped Drive folder, verify input hashes before training, and record the checkpoint hash and held-out results in `docs/CHECKPOINTS.md`.
