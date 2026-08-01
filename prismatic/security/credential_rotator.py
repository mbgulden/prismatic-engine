"""
prismatic.security.credential_rotator — Automated Credential Rotation Daemon.

Manages the lifecycle of external API tokens (GitHub, Linear, OpenAI, Anthropic).
Provides schedule-based rotation and validation, atomic hot-swapping of environment
variables in the running process, and secure file storage (0600 permissions).
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

# Define default paths
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
DEFAULT_SECRETS_DIR = PRISMATIC_HOME / "secrets"

class CredentialRotator:
    def __init__(self, secrets_dir: Path | None = None):
        self.secrets_dir = secrets_dir or DEFAULT_SECRETS_DIR
        self.state_file = self.secrets_dir / "rotation_state.json"

    def load_secrets_to_env(self) -> None:
        """Load saved credentials from the secrets directory directly into os.environ.
        
        This enables the running process (and any spawned subprocesses) to immediately
        use the latest rotated credentials on startup.
        """
        # Mapping from file name to environment variables to set
        mappings = {
            "github_token.txt": ["GITHUB_TOKEN", "GITHUB_APP_INSTALLATION_TOKEN"],
            "linear_key.txt": ["LINEAR_API_KEY"],
            "openai_key.txt": ["OPENAI_API_KEY"],
            "anthropic_key.txt": ["ANTHROPIC_API_KEY"],
        }
        for filename, env_vars in mappings.items():
            path = self.secrets_dir / filename
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        for env_var in env_vars:
                            os.environ[env_var] = content
                            print(f"[CredentialRotator] Loaded {env_var} from {path.name}")
                except Exception as e:
                    print(f"[CredentialRotator] Error loading {filename} to environment: {e}")

    def save_secret(self, filename: str, content: str) -> Path:
        """Write secret to files under self.secrets_dir with 0600 permissions."""
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.secrets_dir.chmod(0o700)
        except Exception:
            pass

        path = self.secrets_dir / filename
        # Atomic/secure write with 0600 permissions
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _get_state(self) -> dict[str, str]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _update_state(self, service: str, timestamp: datetime.datetime) -> None:
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        state = self._get_state()
        state[service] = timestamp.isoformat()
        try:
            # Save state file safely (e.g. 0600 or 0644 since it is metadata)
            fd = os.open(self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[CredentialRotator] Failed to save rotation state: {e}")

    def get_last_rotation_time(self, service: str) -> datetime.datetime | None:
        state = self._get_state()
        val = state.get(service)
        if not val:
            return None
        try:
            return datetime.datetime.fromisoformat(val)
        except Exception:
            return None

    def _make_request(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        retries: int = 3,
        backoff: float = 1.5,
    ) -> tuple[int, str]:
        """Send HTTP request with retry logic (exponential backoff)."""
        headers = headers or {}
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        
        last_err = None
        delay = 1.0
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, response.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                # Client errors (< 500) except 408/429 should fail immediately
                if e.code < 500 and e.code not in (408, 429):
                    raise e
                last_err = e
            except (urllib.error.URLError, Exception) as e:
                last_err = e
            
            if attempt < retries - 1:
                print(f"[CredentialRotator] Request to {url} failed: {last_err}. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= backoff

        raise last_err or RuntimeError(f"Request to {url} failed after {retries} retries")

    def _generate_github_jwt(self, app_id: str, private_key_pem: bytes) -> str:
        """Generate GitHub App JWT signed with private key using cryptography."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes

        private_key = serialization.load_pem_private_key(
            private_key_pem,
            password=None,
        )
        
        header = {"alg": "RS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "iat": now - 60,   # Allow clock skew
            "exp": now + 540,  # 9 minutes lifetime
            "iss": int(app_id),
        }
        
        def b64url(d: dict) -> str:
            jb = json.dumps(d, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(jb).decode("utf-8").rstrip("=")
            
        header_b64 = b64url(header)
        payload_b64 = b64url(payload)
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        
        signature = private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        signature_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def rotate_github(self) -> str:
        """Rotate GitHub App Installation Access Token."""
        app_id = os.environ.get("GITHUB_APP_ID")
        inst_id = os.environ.get("GITHUB_INSTALLATION_ID")
        pem_content = os.environ.get("GITHUB_APP_PRIVATE_KEY")
        pem_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")

        if not app_id or not inst_id:
            raise ValueError("GITHUB_APP_ID and GITHUB_INSTALLATION_ID environment variables must be configured")

        if not pem_content and pem_path:
            pem_content = Path(pem_path).read_text(encoding="utf-8")
        
        if not pem_content:
            raise ValueError("Either GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH must be configured")

        # Generate JWT
        jwt = self._generate_github_jwt(app_id, pem_content.encode("utf-8"))

        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        url = f"{api_url}/app/installations/{inst_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Prismatic-Credential-Rotator",
        }

        print(f"[CredentialRotator] Requesting new GitHub installation token from {url}...")
        status, response_body = self._make_request(url, method="POST", headers=headers)
        data = json.loads(response_body)
        token = data.get("token")
        if not token:
            raise RuntimeError(f"GitHub rotation response missing token: {response_body}")

        # Update environment and store securely
        os.environ["GITHUB_TOKEN"] = token
        os.environ["GITHUB_APP_INSTALLATION_TOKEN"] = token
        self.save_secret("github_token.txt", token)
        self._update_state("github", datetime.datetime.now(datetime.timezone.utc))
        print("[CredentialRotator] GitHub App Installation Token successfully rotated.")
        return token

    def rotate_linear(self) -> str:
        """Rotate Linear API key."""
        current_key = os.environ.get("LINEAR_API_KEY")
        if not current_key:
            # Try loading from secrets file
            path = self.secrets_dir / "linear_key.txt"
            if path.exists():
                current_key = path.read_text(encoding="utf-8").strip()
            
        if not current_key:
            raise ValueError("LINEAR_API_KEY environment variable or linear_key.txt must be available to rotate")

        url = os.environ.get("LINEAR_ROTATION_URL", "https://api.linear.app/v1/keys/rotate")
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Credential-Rotator",
        }
        payload = json.dumps({"old_key": current_key}).encode("utf-8")

        print(f"[CredentialRotator] Rotating Linear API key via {url}...")
        status, response_body = self._make_request(url, method="POST", headers=headers, data=payload)
        data = json.loads(response_body)
        
        # Support either key format
        new_key = data.get("api_key") or data.get("token")
        if not new_key:
            raise RuntimeError(f"Linear rotation response missing new key: {response_body}")

        os.environ["LINEAR_API_KEY"] = new_key
        self.save_secret("linear_key.txt", new_key)
        self._update_state("linear", datetime.datetime.now(datetime.timezone.utc))
        print("[CredentialRotator] Linear API Key successfully rotated.")
        return new_key

    def validate_openai(self) -> bool:
        """Validate OpenAI API key."""
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return False
        
        url = os.environ.get("OPENAI_VALIDATION_URL", "https://api.openai.com/v1/models")
        headers = {
            "Authorization": f"Bearer {key}",
            "User-Agent": "Prismatic-Credential-Rotator",
        }
        try:
            status, _ = self._make_request(url, method="GET", headers=headers, retries=1)
            is_valid = (status == 200)
            self._update_state("openai_validation", datetime.datetime.now(datetime.timezone.utc))
            return is_valid
        except Exception as e:
            print(f"[CredentialRotator] OpenAI validation failed with error: {e}")
            return False

    def rotate_openai(self) -> str:
        """Rotate OpenAI API key."""
        current_key = os.environ.get("OPENAI_API_KEY")
        if not current_key:
            raise ValueError("OPENAI_API_KEY must be set to rotate")

        url = os.environ.get("OPENAI_ROTATION_URL", "https://api.openai.com/v1/keys/rotate")
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Credential-Rotator",
        }
        payload = json.dumps({"old_key": current_key}).encode("utf-8")

        print(f"[CredentialRotator] Rotating OpenAI API key via {url}...")
        status, response_body = self._make_request(url, method="POST", headers=headers, data=payload)
        data = json.loads(response_body)
        new_key = data.get("api_key") or data.get("token")
        if not new_key:
            raise RuntimeError(f"OpenAI rotation response missing new key: {response_body}")

        os.environ["OPENAI_API_KEY"] = new_key
        self.save_secret("openai_key.txt", new_key)
        self._update_state("openai", datetime.datetime.now(datetime.timezone.utc))
        print("[CredentialRotator] OpenAI API Key successfully rotated.")
        return new_key

    def validate_anthropic(self) -> bool:
        """Validate Anthropic API key."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return False
        
        url = os.environ.get("ANTHROPIC_VALIDATION_URL", "https://api.anthropic.com/v1/models")
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "Prismatic-Credential-Rotator",
        }
        try:
            status, _ = self._make_request(url, method="GET", headers=headers, retries=1)
            is_valid = (status == 200)
            self._update_state("anthropic_validation", datetime.datetime.now(datetime.timezone.utc))
            return is_valid
        except Exception as e:
            print(f"[CredentialRotator] Anthropic validation failed with error: {e}")
            return False

    def rotate_anthropic(self) -> str:
        """Rotate Anthropic API key."""
        current_key = os.environ.get("ANTHROPIC_API_KEY")
        if not current_key:
            raise ValueError("ANTHROPIC_API_KEY must be set to rotate")

        url = os.environ.get("ANTHROPIC_ROTATION_URL", "https://api.anthropic.com/v1/keys/rotate")
        headers = {
            "x-api-key": current_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": "Prismatic-Credential-Rotator",
        }
        payload = json.dumps({"old_key": current_key}).encode("utf-8")

        print(f"[CredentialRotator] Rotating Anthropic API key via {url}...")
        status, response_body = self._make_request(url, method="POST", headers=headers, data=payload)
        data = json.loads(response_body)
        new_key = data.get("api_key") or data.get("token")
        if not new_key:
            raise RuntimeError(f"Anthropic rotation response missing new key: {response_body}")

        os.environ["ANTHROPIC_API_KEY"] = new_key
        self.save_secret("anthropic_key.txt", new_key)
        self._update_state("anthropic", datetime.datetime.now(datetime.timezone.utc))
        print("[CredentialRotator] Anthropic API Key successfully rotated.")
        return new_key

    def rotate_service(self, service: str) -> None:
        """Manually trigger rotation for a service (or 'all')."""
        if service == "github":
            self.rotate_github()
        elif service == "linear":
            self.rotate_linear()
        elif service == "openai":
            self.rotate_openai()
        elif service == "anthropic":
            self.rotate_anthropic()
        elif service == "all":
            # Best-effort rotate all
            for svc in ["github", "linear", "openai", "anthropic"]:
                try:
                    self.rotate_service(svc)
                except Exception as e:
                    print(f"[CredentialRotator] Skip rotating {svc}: {e}")
        else:
            raise ValueError(f"Unknown service: {service}")

    def check_and_rotate_all(self) -> None:
        """Adhere to schedules: GitHub App 60m, Linear 30d, AI daily validation + fallback rotation."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # 1. GitHub (60 minutes)
        last_gh = self.get_last_rotation_time("github")
        if not last_gh or (now - last_gh) >= datetime.timedelta(minutes=60):
            try:
                self.rotate_github()
            except Exception as e:
                print(f"[CredentialRotator] Scheduled GitHub rotation failed: {e}. Keeping current credential.")

        # 2. Linear (30 days)
        last_linear = self.get_last_rotation_time("linear")
        if not last_linear or (now - last_linear) >= datetime.timedelta(days=30):
            try:
                self.rotate_linear()
            except Exception as e:
                print(f"[CredentialRotator] Scheduled Linear rotation failed: {e}. Keeping current credential.")

        # 3. OpenAI validation (daily)
        last_openai_val = self.get_last_rotation_time("openai_validation")
        if not last_openai_val or (now - last_openai_val) >= datetime.timedelta(days=1):
            if os.environ.get("OPENAI_API_KEY"):
                valid = self.validate_openai()
                if not valid:
                    print("[CredentialRotator] OpenAI API Key validation failed. Triggering automatic fallback rotation...")
                    try:
                        self.rotate_openai()
                    except Exception as e:
                        print(f"[CredentialRotator] OpenAI fallback rotation failed: {e}")

        # 4. Anthropic validation (daily)
        last_anthropic_val = self.get_last_rotation_time("anthropic_validation")
        if not last_anthropic_val or (now - last_anthropic_val) >= datetime.timedelta(days=1):
            if os.environ.get("ANTHROPIC_API_KEY"):
                valid = self.validate_anthropic()
                if not valid:
                    print("[CredentialRotator] Anthropic API Key validation failed. Triggering automatic fallback rotation...")
                    try:
                        self.rotate_anthropic()
                    except Exception as e:
                        print(f"[CredentialRotator] Anthropic fallback rotation failed: {e}")
