"""Tests for test detection, parsing and baseline comparison."""


from pyfix360.models import ProjectOrigin, TestOutcome
from pyfix360.services import project_service, test_runner


def test_detect_pytest_by_conftest(tmp_path):
    (tmp_path / "conftest.py").write_text("")
    assert test_runner.detect_test_command(tmp_path) == [
        "python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]


def test_detect_none_without_harness(tmp_path):
    assert test_runner.detect_test_command(tmp_path) is None


def test_detect_npm_test(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    assert test_runner.detect_test_command(tmp_path) == ["npm", "test"]


def test_run_tests_no_harness_is_not_run(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    run = test_runner.run_tests(session, project.id, sample_project, phase="baseline")
    # conftest.py exists => pytest IS detected; remove to test NOT_RUN path
    (sample_project / "conftest.py").unlink()
    (sample_project / "tests").rename(sample_project / "not_tests")
    run2 = test_runner.run_tests(session, project.id, sample_project, phase="baseline")
    assert run2.outcome == TestOutcome.NOT_RUN.value
    assert "NOT run" in run2.log_excerpt
    assert run.outcome != TestOutcome.NOT_RUN.value


def test_real_pytest_run_passes(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    run = test_runner.run_tests(session, project.id, sample_project, phase="baseline")
    assert run.outcome == TestOutcome.PASSED.value
    assert run.passed == 1
    assert run.failed == 0
    assert run.command.startswith("python -m pytest")


def test_failing_tests_detected(session, tmp_path):
    proj = tmp_path / "failing"
    (proj / "tests").mkdir(parents=True)
    (proj / "conftest.py").write_text("")
    (proj / "tests" / "test_fail.py").write_text("def test_x():\n    assert 1 == 2\n")
    project = project_service.import_project(session, str(proj), origin=ProjectOrigin.FOLDER)
    run = test_runner.run_tests(session, project.id, proj, phase="baseline")
    assert run.outcome == TestOutcome.FAILED.value
    assert run.failed >= 1


def test_compare_runs_regression_detection():
    class R:
        def __init__(self, outcome, passed=0, failed=0, errors=0):
            self.outcome = outcome
            self.passed, self.failed, self.errors = passed, failed, errors

    base = R("passed", passed=5)
    worse = R("failed", passed=3, failed=2)
    assert test_runner.compare_runs(base, worse) == "regression"

    same = R("passed", passed=5)
    assert test_runner.compare_runs(base, same) == "passed"

    no_baseline_clean = R("passed", passed=2)
    assert test_runner.compare_runs(None, no_baseline_clean) == "passed"

    no_baseline_dirty = R("failed", failed=1)
    assert test_runner.compare_runs(None, no_baseline_dirty) == "failed"

    not_run = R("not_run")
    assert test_runner.compare_runs(base, not_run) == "inconclusive"

    # pre-existing failure count unchanged => improved_not_clean at best
    dirty_base = R("failed", passed=3, failed=2)
    same_dirty = R("failed", passed=4, failed=2)
    assert test_runner.compare_runs(dirty_base, same_dirty) == "improved_not_clean"
