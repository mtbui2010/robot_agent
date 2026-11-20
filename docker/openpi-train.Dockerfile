# Training image for openpi policies (pi0 / pi0.5, JAX), x86-64. Build:  make build-openpi-train   (see docs/training_images.md)
#
# Robot-agnostic: no robot name, no data, no weights. Everything robot- or run-specific is MOUNTED at run time:
#   /data      the LeRobot datasets          /runs   checkpoints, logs, reports      /cache  HF, openpi and XDG caches (weights downloaded once)
#   /code      the project's config module (the openpi TrainConfig for its dataset; openpi itself is never edited)
# The container's command is `vla-train` (init / run / resume / status / stop): `make up-openpi-train` creates a long-lived container with the right flags and mounts.
# Example (a short check that the image sees the GPU):
#   docker run --rm --gpus all --memory 44g --memory-swap 44g --shm-size 8g --user 1000:1000 -v vla-cache:/cache vla-train:openpi \
#       python -c "import jax; print(jax.devices())"
#
# Pins = the recipe of the vlaserve `openpi` backend (vlaserve/data/backends/openpi.yaml): jax 0.5.3, flax 0.10.2, torch 2.7.1,
# openpi and the LeRobot fork it pins, so a policy trained here is served by the same code versions. KEEP THE TWO IN STEP.
# openpi's own scripts (scripts/train.py, compute_norm_stats.py, serve_policy.py) are not part of the installed package, so the
# source is cloned at the pinned commit to /opt/openpi (OPENPI_ROOT) and its scripts are run from there.
#
# Base: python slim, not nvidia/cuda: the CUDA libraries come from the jax[cuda12] and torch wheels as pip packages. The driver comes
# from the host through the NVIDIA container runtime and must support CUDA 12.x. Memory: always run with --memory and --memory-swap
# (a training once took 44 GB of host RAM) and --shm-size (the data loader workers share memory).
FROM python:3.11-slim-bookworm

ARG OPENPI_REV=215abfb217dbac7d5f1273282331b9b1866c0479
ARG LEROBOT_REV=0cf864870cf29f4738d3ade893e6fd13fbd7cdb5

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    OPENPI_ROOT=/opt/openpi \
    HF_HOME=/cache/huggingface \
    OPENPI_DATA_HOME=/cache/openpi \
    XDG_CACHE_HOME=/cache/xdg \
    XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 \
    WANDB_MODE=disabled

# libgl/glib: OpenCV (LeRobot and the openpi data code). git + ca-certificates: the two git installs below. gcc + kernel headers: the
# LeRobot fork depends on pynput -> evdev, which compiles C against linux/input.h. Pipeline-Depth=0: some proxies corrupt pipelined downloads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends -o Acquire::http::Pipeline-Depth=0 -o Acquire::Retries=3 \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 git ca-certificates gcc libc6-dev linux-libc-dev \
 && rm -rf /var/lib/apt/lists/*

# The pins uv.lock forces on openpi (override-dependencies), first, so nothing later moves them. pyarrow / datasets: pyarrow 20+ needs numpy 2.
RUN pip install \
        "jax[cuda12]==0.5.3" flax==0.10.2 torch==2.7.1 "numpy>=1.22.4,<2.0.0" ml-dtypes==0.4.1 tensorstore==0.1.74 \
        orbax-checkpoint==0.11.13 transformers==4.53.2 jaxtyping==0.2.36 chex==0.1.90 beartype==0.19.0 ml_collections==1.0.0 \
        pyarrow==19.0.1 datasets==3.6.0

# openpi's pyproject takes lerobot and openpi-client from [tool.uv.sources], which pip ignores: each is installed from its own git
# revision as a step of its own, in the order that lets the next one find the previous already installed.
RUN pip install "lerobot @ git+https://github.com/huggingface/lerobot@${LEROBOT_REV}" \
 && git clone https://github.com/Physical-Intelligence/openpi.git /opt/openpi \
 && git -C /opt/openpi checkout "${OPENPI_REV}" \
 && pip install /opt/openpi/packages/openpi-client \
 && pip install /opt/openpi

RUN python -c "import jax, openpi.training.config, openpi.policies.policy_config, openpi_client, lerobot; print('openpi training env: jax', jax.__version__)"

# The command of the image: vla-train init / run / resume / status / stop (docker/vla_train, stdlib + PyYAML). Last, so a change of it does not rebuild the layers above.
COPY vla_train /opt/vla_train
RUN pip install /opt/vla_train && vla-train --help > /dev/null
ENV VLA_FAMILY=openpi

# Non-root (the run script passes --user 1000:1000): /cache and /work are owned by it so a named volume mounted there inherits the ownership.
RUN useradd --uid 1000 --create-home vla \
 && mkdir -p /cache/huggingface /cache/openpi /cache/xdg /work /opt/openpi/.cache \
 && chown -R vla:vla /cache /work
USER vla
ENV HOME=/home/vla
WORKDIR /work
CMD ["bash"]
