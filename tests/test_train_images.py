"""The two training Dockerfiles (docker/) and their make targets: robot-agnostic, pinned, no data or weights baked in. No docker needed."""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = {'openpi': ROOT / 'docker' / 'openpi-train.Dockerfile', 'lerobot': ROOT / 'docker' / 'lerobot-train.Dockerfile'}
ROBOT_WORDS = ('kcare', 'kaair', 'clobot', 'stack26')                     # rule 1 of CLAUDE.md: no robot name in the core


def text(name):
    return FILES[name].read_text()


def test_the_images_name_no_robot_and_copy_no_data_or_weights_in():
    for name in FILES:
        body = text(name).lower()
        assert not [w for w in ROBOT_WORDS if w in body], name
        copies = re.findall(r'^\s*(?:COPY|ADD)\s+(\S+)', text(name), re.M)
        assert copies == ['vla_train'], f'{name}: only the vla-train command is copied in; data and weights are mounted, not baked in: {copies}'


def test_the_versions_are_pinned_and_match_the_serving_recipes():
    o, lr = text('openpi'), text('lerobot')
    for pin in ('jax[cuda12]==0.5.3', 'flax==0.10.2', 'torch==2.7.1', 'pyarrow==19.0.1', 'datasets==3.6.0',
                'OPENPI_REV=215abfb217dbac7d5f1273282331b9b1866c0479', 'LEROBOT_REV=0cf864870cf29f4738d3ade893e6fd13fbd7cdb5'):
        assert pin in o, pin
    for pin in ('TORCH_VERSION=2.10.0', 'TORCHVISION_VERSION=0.25.0', 'LEROBOT_VERSION=0.6.1', 'TORCH_CUDA=cu128', 'lerobot[pi,smolvla,training]'):
        assert pin in lr, pin
    assert 'FROM python:3.11' in o and 'FROM python:3.12' in lr             # the Python the vlaserve recipes use


def test_both_images_run_as_a_user_and_leave_the_memory_cap_to_the_caller():
    for name in FILES:
        body = text(name)
        assert 'USER vla' in body and '--memory' in body and '--shm-size' in body       # the header tells the caller to cap RAM and give shm


def test_the_make_targets_build_the_two_files_with_a_version_tag_and_a_short_tag():
    if not shutil.which('make'):
        pytest.skip('no make')
    out = subprocess.run(['make', '-n', 'build-openpi-train', 'build-lerobot-train'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert 'docker/openpi-train.Dockerfile' in out and 'docker/lerobot-train.Dockerfile' in out
    for tag in ('vla-train:openpi-215abfb', 'vla-train:openpi ', 'vla-train:lerobot-0.6.1-cu128', 'vla-train:lerobot '):
        assert tag in out, tag
    custom = subprocess.run(['make', '-n', 'build-openpi-train', 'TRAIN_IMAGE=me/vla'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert 'me/vla:openpi' in custom


def test_the_up_targets_make_a_capped_long_lived_container_and_need_the_data_and_runs_folders():
    if not shutil.which('make'):
        pytest.skip('no make')
    out = subprocess.run(['make', '-n', 'up-openpi-train', 'DATA=/d', 'RUNS=/r', 'CODE=/c'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for part in ('--name openpi-train', '--gpus all', '--memory 44g --memory-swap 44g', '--shm-size 8g', '--oom-score-adj 1000', '-v "/d":/data:ro', '-v "/r":/runs',
                 '-v "/c":/code:ro', 'vla-train:openpi', 'sleep infinity'):
        assert part in out, part
    no_code = subprocess.run(['make', '-n', 'up-lerobot-train', 'DATA=/d', 'RUNS=/r', 'TRAIN_MEM=20g'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert '/code' not in no_code and '--memory 20g' in no_code and 'vla-train:lerobot' in no_code and '--name lerobot-train' in no_code
    missing = subprocess.run(['make', 'up-openpi-train'], cwd=ROOT, capture_output=True, text=True)
    assert missing.returncode != 0 and 'usage: make up-' in (missing.stdout + missing.stderr)


def test_the_host_wrapper_is_valid_shell_and_the_command_is_copied_into_both_images():
    wrapper = ROOT / 'docker' / 'vla-train'
    assert subprocess.run(['bash', '-n', str(wrapper)]).returncode == 0
    assert subprocess.run([str(wrapper)], capture_output=True, text=True).returncode == 2                   # no family: usage
    for name in FILES:
        assert 'COPY vla_train /opt/vla_train' in text(name) and f'VLA_FAMILY={name}' in text(name)
