from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.devices import StopMode
from src.execution import ExecutionStateError
from src.robot_server.contracts import ServerRequest, TaskApi
from src.robot_server.security import ServerAccessController


def setup_api():
    access = ServerAccessController("key", control_lease_seconds=30)
    access.register("client", "local")
    execution = Mock()
    execution.snapshot.return_value = SimpleNamespace(run_id="active-run")
    safety = Mock()
    safety.stop.return_value.to_dict.return_value = {"complete": False, "errors": ["execution still active"]}
    return TaskApi(Mock(), access, execution=execution, safety=safety), execution, safety


def request(api, command):
    return api.dispatch(ServerRequest("client", "request", command, {}, "test"))


@pytest.mark.parametrize("command", ["execution.pause", "execution.resume", "safety.emergency_stop"])
def test_controls_require_authentication_and_control_lease(command):
    api, execution, safety = setup_api()
    with pytest.raises(Exception) as error:
        request(api, command)
    assert error.value.code == "authentication_required"
    api.access.authenticate("client", "key")
    with pytest.raises(Exception) as error:
        request(api, command)
    assert error.value.code == "control_required"
    execution.pause.assert_not_called()
    execution.resume.assert_not_called()
    safety.stop.assert_not_called()


def test_pause_resume_and_emergency_report_do_not_claim_physical_completion():
    api, execution, safety = setup_api()
    api.access.authenticate("client", "key")
    api.access.acquire_control("client")
    assert request(api, "execution.pause")["status"] == "pause_requested"
    execution.pause.assert_called_once()
    assert request(api, "execution.resume")["status"] == "resume_requested"
    execution.resume.assert_called_once()
    result = request(api, "safety.emergency_stop")
    safety.stop.assert_called_once_with(StopMode.EMERGENCY)
    assert result["report"]["complete"] is False
    execution.pause.side_effect = ExecutionStateError("not running")
    with pytest.raises(Exception) as error:
        request(api, "execution.pause")
    assert error.value.code == "invalid_execution_state"
