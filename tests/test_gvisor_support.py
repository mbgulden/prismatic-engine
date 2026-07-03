import json
from unittest.mock import patch, MagicMock
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager

def test_gvisor_detection():
    with patch("subprocess.run") as mock_run:
        # Mock runsc version success
        mock_run.return_value = MagicMock(returncode=0, stdout="runsc version ...")

        runtime = SandboxPodManager._detect_runtime()
        assert runtime == "gvisor"
        mock_run.assert_any_call(["runsc", "--version"], capture_output=True, text=True, timeout=5)

def test_kubectl_version_client():
    with patch("subprocess.run") as mock_run:
        # Mock runsc fail, docker fail, kubectl success
        def side_effect(cmd, **kwargs):
            if cmd[0] == "runsc":
                raise FileNotFoundError()
            if cmd[0] == "docker":
                raise FileNotFoundError()
            if cmd[0] == "kubectl":
                return MagicMock(returncode=0, stdout="Client Version: ...")
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect

        runtime = SandboxPodManager._detect_runtime()
        assert runtime == "k3s"
        mock_run.assert_any_call(["kubectl", "version", "--client"], capture_output=True, text=True, timeout=5)

def test_gvisor_docker_command():
    mgr = SandboxPodManager(runtime="gvisor", runtime_class="my-runsc")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id")

        result = mgr.start_pod("test-plugin", {"image": "my-image"})

        assert result["runtime"] == "gvisor"
        assert result["runtime_class"] == "my-runsc"

        # Check if --runtime my-runsc was passed to docker run
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert "docker" in cmd
        assert "run" in cmd
        assert "--runtime" in cmd
        assert "my-runsc" in cmd

def test_k3s_overrides():
    mgr = SandboxPodManager(runtime="k3s", runtime_class="gvisor")
    with patch("subprocess.run") as mock_run:
        # Mock kubectl run and status check
        def side_effect(cmd, **kwargs):
            if "run" in cmd:
                return MagicMock(returncode=0, stdout="pod/test-plugin created")
            if "get" in cmd:
                return MagicMock(returncode=0, stdout="Running")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect

        result = mgr.start_pod("test-plugin", {"image": "my-image"})

        # Check if --overrides with runtimeClassName was passed
        args, kwargs = mock_run.call_args_list[0]
        cmd = args[0]
        assert "kubectl" in cmd
        assert "run" in cmd
        assert "--overrides" in cmd

        overrides_idx = cmd.index("--overrides") + 1
        overrides = json.loads(cmd[overrides_idx])
        assert overrides["spec"]["runtimeClassName"] == "gvisor"

if __name__ == "__main__":
    test_gvisor_detection()
    test_kubectl_version_client()
    test_gvisor_docker_command()
    test_k3s_overrides()
    print("All gVisor verification tests passed!")
