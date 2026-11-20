# Training images (docker)

Two Dockerfiles in `docker/`, one per policy family. **Robot-agnostic**: no robot name, no data, no weights; everything robot- or run-specific is mounted
when the container runs.

| Image | Dockerfile | For | Build |
|---|---|---|---|
| `vla-train:openpi` (and `:openpi-215abfb`) | `docker/openpi-train.Dockerfile` | openpi pi0 / pi0.5 (JAX): `scripts/train.py`, norm stats, serving | `make build-openpi-train` |
| `vla-train:lerobot` (and `:lerobot-0.6.1-cu128`) | `docker/lerobot-train.Dockerfile` | LeRobot policies (PyTorch): `lerobot-train` | `make build-lerobot-train` |

openpi 22.8 GB (JAX and torch each bring their own CUDA libraries), LeRobot 14.4 GB; 5-15 minutes to build with a warm network. `make build-... TRAIN_IMAGE=me/vla-train` names them for a registry; `DOCKER_BUILD_ARGS=--no-cache` rebuilds from scratch.

## Use: a container per family, `vla-train` inside
```bash
make build-openpi-train                                   # the image (once, or when a pin changes)
make up-openpi-train DATA=/path/datasets RUNS=/path/runs CODE=/path/project_code    # the container "openpi-train", idle, RAM-capped
docker/vla-train openpi init                              # checks GPU / RAM cap / disk / mounts; fetches the base weights (first time, large)
docker/vla-train openpi run /runs/sd_v1.run.yaml          # preflight, norm stats, smoke test, training, validation   (paths are INSIDE the container)
docker/vla-train openpi status                            # progress, loss, speed, alerts
docker/vla-train openpi resume sd_v1                      # after a crash or a stop
docker/vla-train openpi stop sd_v1
```
The same for LeRobot with `lerobot` instead of `openpi`. `docker/vla-train` only runs `docker exec <family>-train vla-train ...` (`run` and `resume` in the background, so they outlive the terminal).
`make down-openpi-train` removes the container; nothing is lost: data, checkpoints and the weights cache are mounted from outside. A container restart stops a run that was going; `resume` continues it.

- **RAM is capped when the container is created** (`TRAIN_MEM`, default 44g, `--memory-swap` the same; a training once took 44 GB of host RAM and the kernel killed the terminal service) and `--shm-size` (`TRAIN_SHM`) for the data loader workers.
- **Mounts:** `/data` datasets (read-only), `/runs` everything a run writes (state, logs, checkpoints, reports), `/cache` the downloaded weights (a named volume `vla-cache`; use `TRAIN_CACHE=/some/folder` when your user id is not 1000), `/code` the project's config module (read-only; openpi: a file with `register()` that adds the `TrainConfig`; openpi itself is never edited; `OPENPI_ROOT=/opt/openpi` holds the pinned source whose `scripts/` are run). Inside the container the environment variables `VLA_RUNS`, `VLA_DATA`, `VLA_CODE`, `VLA_CACHE` give these paths: a project config should read them instead of fixed paths.
- The host needs docker, the NVIDIA container toolkit and a driver for CUDA 12.x (lerobot image: CUDA >= 12.8, driver >= 570; older: `--build-arg TORCH_CUDA=cu126` and the matching torch pins).

## The run file
```yaml
name: sd_v1                  # the run's folder under /runs
family: openpi               # openpi | lerobot; must match the container
steps: 5000
batch_size: 16
smoke_steps: 40              # a short test first (0 = none); a failed test stops before the long run
min_free_gpu_gb: 18          # refuse to start when another job leaves less than this free on the GPU
openpi:
  config: my_train_config                         # the TrainConfig name
  code_module: /code/my_config.py                 # register() adds it
  norm_stats: /runs/assets/my_train_config/my_data/norm_stats.json    # computed first when this file is missing
validate:                                          # after a clean end; {run_dir} {name} {ckpt_root} are filled in
  command: [python, /code/val_loss.py, --config, my_train_config, --ckpt-root, "{ckpt_root}", --out, "{run_dir}/val.log"]
```
LeRobot: `lerobot: {args: [--policy.type=smolvla, --dataset.repo_id=local/x, --dataset.root=/data/x]}` instead of `openpi:`; `vla-train` adds `--steps`, `--batch_size`, `--output_dir`, `--job_name`.
Unknown keys and wrong types are errors that name the key.

## What `run` does
1. **Preflight:** a GPU with enough free memory, a RAM limit on the container, writable `/runs` and `/cache`, `/data` mounted. Each problem is named; nothing starts when one is found.
2. **Norm stats** (openpi, when the file is missing). 3. **Smoke test** (`smoke_steps`): judged by the exit code, the log (nan, out of memory, a traceback) and a checkpoint written. A failed test says why and stops.
4. **Training**, log with a time in front of every line (`/runs/<name>/train.log`), ending with `EXIT rc=N`. 5. **Validation:** your `validate.command` runs by itself after a clean end, then `validation.md` is written from `val.log`
(the table of validation loss per step and the lowest one). A run that failed is not validated.
- One run at a time (a lock in `/runs`): a second `run` says which one is in progress. A new run never overwrites checkpoints: an existing name must be `resume`d or renamed.
- State is on disk (`/runs/<name>/state.json`), so `status` and `stop` work from any `docker exec`.

## Versions
The pins are the recipes of the vlaserve backends (`vlaserve/data/backends/openpi.yaml`, `lerobot.yaml`; the LeRobot image adds the `training` extra): a policy trained here is served by the same code versions.
Change a pin in both places. `tests/test_train_images.py` checks the files and the make targets, `tests/test_vla_train.py` the command (stage order, messages, status) with fake commands; neither builds an image or needs a GPU.

## Checked
- **openpi, end to end, in the container (2026-11-19):** `make up-openpi-train`, `vla-train init` (weights found in the cache), then `vla-train run` on the kcare data: norm stats computed (identical to the host's), a 20-step smoke test, 60 training steps (11.5 steps/min, the same speed as on the host), validation of the checkpoint, `validation.md`, state `done`. The first try failed in the norm-stats stage (a PicklingError: the project module was loaded from a file spec, not by name); fixed in `openpi_runner` with a test.
- **LeRobot:** the image builds and `lerobot-train --help` shows the flags the command uses (`--steps`, `--batch_size`, `--output_dir`, `--job_name`, `--resume`, `--config_path`); no LeRobot training has been run through `vla-train` yet.
- Not covered: a generic validation job (the project supplies `validate.command`; for openpi a validation-loss script) and `resume` / `stop` on a real run (both are tested with fake commands).
