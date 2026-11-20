"""docker/vla_train: the `vla-train` command of the training images. Pure logic and the stage order with fake commands: no docker, GPU or framework."""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'docker' / 'vla_train'))
yaml = pytest.importorskip('yaml')

from vla_train import families, orchestrate, preflight, report, runfile, state, status  # noqa: E402
from vla_train.cli import describe  # noqa: E402
from vla_train.paths import Paths  # noqa: E402

OPENPI = """
name: sd_v1
family: openpi
steps: 5000
batch_size: 16
smoke_steps: 40
openpi: {config: my_cfg, code_module: /code/my.py, norm_stats: NORM}
validate: {command: [python, /code/val.py, --out, "{run_dir}/val.log", --ckpt-root, "{ckpt_root}"]}
"""
LEROBOT = "name: lr1\nfamily: lerobot\nsteps: 100\nbatch_size: 8\nlerobot: {args: [--policy.type=smolvla, --dataset.repo_id=local/x]}\n"


@pytest.fixture
def paths(tmp_path):
    for d in ('runs', 'data', 'code', 'cache'):
        (tmp_path / d).mkdir()
    return Paths(tmp_path / 'runs', tmp_path / 'data', tmp_path / 'code', tmp_path / 'cache')


def run_of(paths, text=OPENPI):
    return runfile.parse(text.replace('NORM', str(paths.runs / 'norm_stats.json')))


# -- the run file ---------------------------------------------------------------------------------------------------------------------
def test_a_good_run_file_parses_with_defaults(paths):
    r = run_of(paths)
    assert (r.name, r.family, r.steps, r.batch_size, r.smoke_steps) == ('sd_v1', 'openpi', 5000, 16, 40) and r.openpi['config'] == 'my_cfg'
    assert runfile.parse(LEROBOT).smoke_steps == 40 and runfile.parse(LEROBOT).min_free_gpu_gb == 18


@pytest.mark.parametrize('bad, word', [
    ("name: a b\nfamily: openpi\nsteps: 1\nbatch_size: 1\nopenpi: {config: c}", 'name'),
    ("name: a\nfamily: tensorflow\nsteps: 1\nbatch_size: 1", 'family'),
    ("name: a\nfamily: openpi\nsteps: 0\nbatch_size: 1\nopenpi: {config: c}", 'steps'),
    ("name: a\nfamily: openpi\nsteps: 1\nbatch_size: 1", 'openpi.config'),
    ("name: a\nfamily: lerobot\nsteps: 1\nbatch_size: 1", 'lerobot.args'),
    ("name: a\nfamily: openpi\nsteps: 1\nbatch_size: 1\nopenpi: {config: c, speling: 1}", 'speling'),
    ("name: a\nfamily: openpi\nsteps: 1\nbatch_size: 1\nopenpi: {config: c}\nstepz: 3", 'stepz'),
    ("name: a\nfamily: openpi\nsteps: 1\nbatch_size: 1\nopenpi: {config: c}\nvalidate: {command: val.py}", 'validate.command'),
    ("- a list", 'mapping'),
])
def test_a_bad_run_file_says_which_part_is_wrong(bad, word):
    with pytest.raises(runfile.RunFileError, match=word):
        runfile.parse(bad)


# -- commands -------------------------------------------------------------------------------------------------------------------------
def test_openpi_commands_register_the_project_config_and_keep_checkpoints_in_the_run(paths):
    r = run_of(paths)
    cmd = families.train_cmd(r, paths)
    assert cmd[1:4] == ['-m', 'vla_train.openpi_runner', '/code/my.py'] and 'train.py' in cmd and 'my_cfg' in cmd
    assert '--exp-name=sd_v1' in cmd and '--batch-size=16' in cmd and '--num-train-steps=5000' in cmd
    assert f'--checkpoint-base-dir={paths.runs}/sd_v1/checkpoints' in cmd and '--resume' not in cmd and '--overwrite' not in cmd
    smoke = families.train_cmd(r, paths, smoke=True)
    assert '--exp-name=sd_v1_smoke' in smoke and '--num-train-steps=40' in smoke and '--overwrite' in smoke and 'smoke_checkpoints' in ' '.join(smoke)
    assert '--resume' in families.train_cmd(r, paths, resume=True)
    assert families.ckpt_root(r, paths) == paths.runs / 'sd_v1' / 'checkpoints' / 'my_cfg' / 'sd_v1'
    assert families.norm_stats_cmd(r)[-3:] == ['compute_norm_stats.py', '--config-name', 'my_cfg'] or 'compute_norm_stats.py' in families.norm_stats_cmd(r)


def test_lerobot_commands_pass_the_arguments_and_add_steps_batch_and_output(paths):
    r = runfile.parse(LEROBOT)
    cmd = families.train_cmd(r, paths)
    assert cmd[0] == 'lerobot-train' and '--policy.type=smolvla' in cmd and '--steps=100' in cmd and '--batch_size=8' in cmd
    assert f'--output_dir={paths.runs}/lr1/checkpoints' in cmd
    assert '--resume=true' in families.train_cmd(r, paths, resume=True) and families.norm_stats_cmd(r) is None


def test_a_checkpoint_folder_still_being_written_does_not_count(paths):
    r = run_of(paths)
    root = families.ckpt_root(r, paths)
    assert not families.has_checkpoint(r, paths)
    (root / '1000.orbax-checkpoint-tmp').mkdir(parents=True)
    assert not families.has_checkpoint(r, paths)
    (root / '1000').mkdir()
    assert families.has_checkpoint(r, paths)


def test_the_smoke_test_is_judged_by_exit_code_log_and_checkpoint(paths):
    r = run_of(paths)
    assert 'exit code 3' in families.smoke_problem(r, paths, 3, '')
    assert 'nan' in families.smoke_problem(r, paths, 0, 'Step 0: loss=nan')
    assert 'no checkpoint' in families.smoke_problem(r, paths, 0, 'Step 0: loss=0.03, grad_norm=0.2')
    (families.ckpt_root(r, paths, smoke=True) / '39').mkdir(parents=True)
    assert families.smoke_problem(r, paths, 0, 'Step 0: loss=0.03, grad_norm=0.2 maintenance') == ''          # 'maintenance' is not a nan


# -- the log --------------------------------------------------------------------------------------------------------------------------
def test_status_reads_steps_speed_eta_and_the_end_marker():
    t0 = 1_700_000_000
    lines = [f"{t0 + i * 60} Step {i * 100}: loss={0.05 - i * 0.001:.4f}, grad_norm=0.1" for i in range(1, 4)] + [f"{t0 + 300} EXIT rc=0"]
    s = status.summarize("\n".join(lines), 5000, alive=False)
    assert s['step'] == 300 and s['loss'] == pytest.approx(0.047) and s['steps_per_min'] == pytest.approx(100.0) and s['eta_min'] == pytest.approx(47.0)
    assert s['ended'] == 'ok' and s['alerts'] == []
    assert status.summarize("x\n5 EXIT rc=1", 10)['ended'] == 'rc=1'
    assert status.parse(["step:1K smpl:8K loss:0.250 grdn:2"])[0][1:] == (1000, 0.25)                # the LeRobot style


def test_status_alerts_on_nan_out_of_memory_traceback_and_a_stall():
    assert any(a.startswith('NAN') for a in status.summarize("10 Step 100: loss=nan", 1000)['alerts'])
    assert any(a.startswith('OOM') for a in status.summarize("RESOURCE_EXHAUSTED: Out of memory", 1000)['alerts'])
    assert any(a.startswith('ERROR') for a in status.summarize("Traceback (most recent call last):", 1000)['alerts'])
    stall = status.summarize("1 Step 1: loss=0.1", 100, alive=True, mtime=0.0, now=2000.0)
    assert any(a.startswith('STALL') for a in stall['alerts'])
    assert not status.summarize("1 Step 1: loss=0.1", 100, alive=False, mtime=0.0, now=2000.0)['alerts']       # a finished run is not stalled


def test_the_validation_report_names_the_lowest_step():
    md = report.validation_md('T', "step=1000 val_loss=0.015 train_eval=0.011 frames=1\nstep=3000 val_loss=0.0139 train_eval=0.006 frames=1\nstep=5000 val_loss=0.0176 train_eval=0.004 frames=1\n")
    assert 'step 3000 (0.01390)' in md and '| 5000 |' in md and 'No validation' in report.validation_md('T', '')


# -- lock and preflight ---------------------------------------------------------------------------------------------------------------
def test_only_one_run_at_a_time_and_a_dead_lock_is_taken_over(paths):
    f = state.lock(paths.runs, 'a')
    assert f.is_file()
    f.write_text('{"name": "other", "pid": %d}' % os.getppid())               # a live process that is not us
    with pytest.raises(state.Busy, match='other'):
        state.lock(paths.runs, 'b')
    f.write_text('{"name": "dead", "pid": 2147483000}')
    assert state.lock(paths.runs, 'c').is_file()
    state.unlock(f)
    assert not f.exists()


def test_preflight_names_each_problem(paths):
    ok = preflight.problems(paths, 18, gpu=lambda: 22.0, cap=lambda: 44.0)
    assert ok == []
    bad = preflight.problems(paths, 18, gpu=lambda: None, cap=lambda: None)
    assert any('--gpus all' in p for p in bad) and any('--memory' in p for p in bad)
    assert any('only 5.0 GB free' in p for p in preflight.problems(paths, 18, gpu=lambda: 5.0, cap=lambda: 44.0))
    assert any('not mounted' in p for p in preflight.problems(Paths(paths.runs, paths.data / 'nope', paths.code, paths.cache), 1, gpu=lambda: 20.0, cap=lambda: 1.0))


# -- the stages with fake commands ----------------------------------------------------------------------------------------------------
class FakeRunner:
    """Stands in for the training commands: records what ran and makes the files the real ones make."""
    def __init__(self, paths, run, fail=None):
        self.paths, self.run, self.fail, self.calls = paths, run, fail, []

    def __call__(self, cmd, log, run_dir, env=None, cwd=None):
        text = ' '.join(cmd)
        kind = 'norm' if 'compute_norm_stats' in text else 'smoke' if ('_smoke' in text or 'smoke_checkpoints' in text) else 'train' if ('train.py' in text or 'lerobot-train' in text) else 'validate'
        self.calls.append(kind)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('1 Step 0: loss=nan\n' if self.fail == kind + '_nan' else '1 Step 0: loss=0.03, grad_norm=0.2\n')
        if self.fail == kind:
            return 3
        if kind == 'norm':
            pathlib.Path(self.run.openpi['norm_stats']).write_text('{}')
        if kind in ('smoke', 'train'):
            (families.ckpt_root(self.run, self.paths, smoke=kind == 'smoke') / '9').mkdir(parents=True)
        if kind == 'validate':
            (run_dir / 'val.log').write_text('step=9 val_loss=0.01 train_eval=0.005 frames=4\n')
        return 0


def go(paths, fail=None, resume=False, text=OPENPI, **kw):
    r = run_of(paths, text)
    fr = FakeRunner(paths, r, fail)
    msgs = []
    rc = orchestrate.execute(r, paths, resume=resume, runner=fr, check=lambda p, g: kw.get('problems', []), out=msgs.append)
    return rc, fr, msgs, r


def test_a_clean_run_goes_through_every_stage_in_order_and_writes_the_report(paths):
    rc, fr, msgs, r = go(paths)
    assert rc == 0 and fr.calls == ['norm', 'smoke', 'train', 'validate']
    st = state.read(paths.run_dir('sd_v1'))
    assert st['stage'] == 'done' and 'validated' in st['message']
    assert '| 9 |' in (paths.run_dir('sd_v1') / 'validation.md').read_text()
    assert (paths.run_dir('sd_v1') / 'train.log').read_text().rstrip().endswith('EXIT rc=0')
    assert not (paths.runs / '.vla_train.lock').exists()                                   # the lock is released


def test_norm_stats_are_computed_only_when_missing(paths):
    r = run_of(paths)
    pathlib.Path(r.openpi['norm_stats']).write_text('{}')
    assert go(paths)[1].calls == ['smoke', 'train', 'validate']


@pytest.mark.parametrize('fail, stage', [('norm', 'norm_stats'), ('smoke', 'smoke'), ('smoke_nan', 'smoke'), ('train', 'train')])
def test_a_failed_stage_stops_the_run_and_says_where_and_why(paths, fail, stage):
    rc, fr, msgs, _ = go(paths, fail=fail)
    st = state.read(paths.run_dir('sd_v1'))
    assert rc == 1 and st['stage'] == 'failed' and st['failed_at'] == stage and st['message'] and any('FAILED' in m for m in msgs)
    assert 'train' not in fr.calls[:fr.calls.index('smoke') + 1 if 'smoke' in fr.calls else 0] and 'validate' not in fr.calls


def test_a_failed_training_says_how_to_resume(paths):
    _, _, msgs, _ = go(paths, fail='train')
    assert any('vla-train resume sd_v1' in m for m in msgs)


def test_preflight_problems_stop_everything_before_any_command(paths):
    rc, fr, msgs, _ = go(paths, problems=['no GPU'])
    assert rc == 1 and fr.calls == [] and state.read(paths.run_dir('sd_v1'))['failed_at'] == 'preflight'


def test_a_new_run_never_overwrites_checkpoints_and_resume_needs_them(paths):
    r = run_of(paths)
    rc, fr, msgs, _ = go(paths, resume=True)
    assert rc == 1 and any('nothing to resume' in m for m in msgs)
    (families.ckpt_root(r, paths) / '1000').mkdir(parents=True)
    rc, fr, msgs, _ = go(paths)
    assert rc == 1 and fr.calls == [] and any('already has checkpoints' in m for m in msgs)
    rc, fr, msgs, _ = go(paths, resume=True)
    assert rc == 0 and fr.calls == ['train', 'validate']                                  # a resume skips norm stats and the smoke test


def test_a_run_without_a_validation_command_just_trains(paths):
    rc, fr, _, _ = go(paths, text=LEROBOT)
    assert rc == 0 and fr.calls == ['smoke', 'train']


def test_describe_shows_stage_progress_and_alerts(paths):
    d = paths.run_dir('sd_v1')
    state.write(d, name='sd_v1', stage='train', message='5000 steps', steps=5000)
    (d / 'train.log').write_text('1700000000 Step 100: loss=0.05\n1700000060 Step 200: loss=0.04\n')
    out = describe(paths, 'sd_v1')
    assert 'sd_v1: train' in out and 'step 200/5000' in out and 'steps/min' in out
    assert 'no such run' in describe(paths, 'zzz')


def test_the_project_module_is_importable_by_name_so_its_classes_can_be_pickled(tmp_path):
    import pickle

    from vla_train import openpi_runner
    (tmp_path / 'proj_cfg_for_test.py').write_text('class Subset:\n    pass\n\ndef register():\n    pass\n')
    mod = openpi_runner.load_module(str(tmp_path / 'proj_cfg_for_test.py'))
    obj = mod.Subset()
    assert type(pickle.loads(pickle.dumps(obj))).__name__ == 'Subset' and mod.__name__ == 'proj_cfg_for_test' and hasattr(mod, 'register')
