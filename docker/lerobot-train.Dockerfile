# Training image for LeRobot policies (pi0 / pi0.5 / SmolVLA ... in PyTorch), x86-64. Build:  make build-lerobot-train   (see docs/training_images.md)
#
# Robot-agnostic: no robot name, no data, no weights. Everything robot- or run-specific is MOUNTED at run time:
#   /data      the LeRobot datasets          /runs   checkpoints, logs, reports      /cache  HF cache (weights downloaded once)
# The container's command is `vla-train` (init / run / resume / status / stop): `make up-lerobot-train` creates a long-lived container with the right flags and mounts.
# Example (train SmolVLA on a dataset mounted at /data/my_dataset):
#   docker run -d --gpus all --memory 44g --memory-swap 44g --shm-size 8g --user 1000:1000 -v /path/data:/data:ro -v /path/runs:/runs \
#       -v vla-cache:/cache vla-train:lerobot lerobot-train --dataset.repo_id=local/my_dataset --dataset.root=/data/my_dataset \
#       --policy.type=smolvla --output_dir=/runs/my_run
#
# Pins = the recipe of the vlaserve `lerobot` backend (vlaserve/data/backends/lerobot.yaml): lerobot[pi,smolvla]==0.6.1 plus the `training` extra (datasets, accelerate: the serving recipe does not need them), Python 3.12,
# torch 2.10.0 built for CUDA 12.8, so a policy trained here is served by the same code versions. KEEP THE TWO IN STEP.
#
# Base: python slim, not nvidia/cuda: the CUDA 12.8 libraries come with the torch wheel (cu128). The driver comes from the host through the NVIDIA
# container runtime and must support CUDA >= 12.8 (driver >= 570); with an older driver build with --build-arg TORCH_CUDA=cu126 (the torch / torchvision
# pins below name a cu128 build: change them to the matching +cu126 files). The default PyPI torch is cu130 and does not see a 12.8 driver's GPU.
# Memory: always run with --memory and --memory-swap (a training once took 44 GB of host RAM) and --shm-size (data loader workers share memory).
FROM python:3.12-slim-bookworm

ARG TORCH_CUDA=cu128
ARG TORCH_VERSION=2.10.0
ARG TORCHVISION_VERSION=0.25.0
ARG LEROBOT_VERSION=0.6.1

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache/xdg \
    WANDB_MODE=disabled

# libgl/glib: OpenCV. ffmpeg: the video decoders of the datasets (torchcodec / PyAV). git + ca-certificates: a dependency may be installed from git.
# gcc + kernel headers: pynput -> evdev compiles C against linux/input.h. Pipeline-Depth=0: some proxies corrupt pipelined downloads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends -o Acquire::http::Pipeline-Depth=0 -o Acquire::Retries=3 \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg git ca-certificates gcc libc6-dev linux-libc-dev \
 && rm -rf /var/lib/apt/lists/*

# torch first, from the CUDA index, so lerobot finds it installed and does not pull the default build.
RUN pip install --extra-index-url "https://download.pytorch.org/whl/${TORCH_CUDA}" \
        "torch==${TORCH_VERSION}+${TORCH_CUDA}" "torchvision==${TORCHVISION_VERSION}+${TORCH_CUDA}" \
 && pip install --extra-index-url "https://download.pytorch.org/whl/${TORCH_CUDA}" "lerobot[pi,smolvla,training]==${LEROBOT_VERSION}"

RUN python -c "import torch, lerobot; print('lerobot training env: torch', torch.__version__)" && lerobot-train --help > /dev/null

# The command of the image: vla-train init / run / resume / status / stop (docker/vla_train, stdlib + PyYAML). Last, so a change of it does not rebuild the layers above.
COPY vla_train /opt/vla_train
RUN pip install /opt/vla_train && vla-train --help > /dev/null
ENV VLA_FAMILY=lerobot

# Non-root (the run script passes --user 1000:1000): /cache and /work are owned by it so a named volume mounted there inherits the ownership.
RUN useradd --uid 1000 --create-home vla \
 && mkdir -p /cache/huggingface /cache/xdg /work \
 && chown -R vla:vla /cache /work
USER vla
ENV HOME=/home/vla
WORKDIR /work
CMD ["bash"]
