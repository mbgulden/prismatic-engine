import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.agent_context import MANAGED_BLOCK_START, install_context_doc, list_context_cards, render_context_lines


def _pick_available_skill(client: TestClient) -> str:
    payload = client.get('/api/skills').json()
    assert payload['source'] == 'prismatic.skills'
    assert payload['bundled_count'] >= 1
    for skill in payload['skills']:
        if not skill['installed']:
            return skill['id']
    raise AssertionError('expected at least one uninstalled bundled skill in isolated HOME')


def test_skills_api_install_uninstall_emits_timeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PRISMATIC_STATE_DIR', str(tmp_path / 'state'))
    from prismatic.gateway.server import app

    client = TestClient(app)
    skill = _pick_available_skill(client)

    info = client.get(f'/api/skills/{skill}')
    assert info.status_code == 200
    assert info.json()['skill']['id'] == skill

    install = client.post(f'/api/skills/{skill}/install')
    assert install.status_code == 200, install.text
    install_payload = install.json()
    assert install_payload['ok'] is True
    assert install_payload['skill']['installed'] is True
    assert install_payload['timeline_item']['source'] == 'SkillRegistry'
    assert (tmp_path / 'home' / '.prismatic' / 'skills' / skill).is_dir()

    duplicate = client.post(f'/api/skills/{skill}/install')
    assert duplicate.status_code == 409

    timeline = client.get('/api/timeline?source=SkillRegistry')
    assert any(item['title'] == 'Skill installed' and item['entity_id'] == skill for item in timeline.json()['items'])

    uninstall = client.post(f'/api/skills/{skill}/uninstall')
    assert uninstall.status_code == 200, uninstall.text
    assert uninstall.json()['timeline_item']['source'] == 'SkillRegistry'
    assert not (tmp_path / 'home' / '.prismatic' / 'skills' / skill).exists()

    missing_uninstall = client.post(f'/api/skills/{skill}/uninstall')
    assert missing_uninstall.status_code == 404
    unknown = client.get('/api/skills/not-a-real-skill')
    assert unknown.status_code == 404

    timeline = client.get('/api/timeline?source=SkillRegistry')
    titles = [item['title'] for item in timeline.json()['items']]
    assert 'Skill installed' in titles
    assert 'Skill uninstalled' in titles


def test_agent_context_api_install_doc_emits_timeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PRISMATIC_STATE_DIR', str(tmp_path / 'state'))
    from prismatic.gateway.server import app

    client = TestClient(app)
    cards = client.get('/api/agent-context?agent=agy')
    assert cards.status_code == 200
    assert cards.json()['source'] == 'prismatic.agent_context'
    assert any(card['id'] == 'skills' for card in cards.json()['cards'])

    line = client.get('/api/agent-context/line?agent=openclaw')
    assert line.status_code == 200
    assert 'Prismatic Skills Registry' in line.json()['line']

    doc = tmp_path / 'AGENTS.md'
    doc.write_text('# Human notes\n\nKeep this.', encoding='utf-8')
    installed = client.post('/api/agent-context/install-doc', json={'path': str(doc), 'agent': 'hermes'})
    assert installed.status_code == 200, installed.text
    payload = installed.json()
    assert payload['ok'] is True
    assert payload['timeline_item']['source'] == 'AgentContext'
    text = doc.read_text(encoding='utf-8')
    assert '# Human notes' in text
    assert MANAGED_BLOCK_START in text
    assert 'Prismatic Skills Registry' in text

    timeline = client.get('/api/timeline?source=AgentContext')
    assert any(item['title'] == 'Agent context doc installed' and item['entity_id'] == str(doc) for item in timeline.json()['items'])
    assert client.post('/api/agent-context/install-doc', json={'agent': 'hermes'}).status_code == 400


def test_agent_context_module_preserves_human_content(tmp_path: Path) -> None:
    assert any(card['id'] == 'timeline' for card in list_context_cards('hermes'))
    assert 'Operational Timeline' in render_context_lines('agy')
    doc = tmp_path / 'soul.md'
    doc.write_text('before\n', encoding='utf-8')
    first = install_context_doc(doc, agent='openclaw')
    second = install_context_doc(doc, agent='openclaw')
    text = doc.read_text(encoding='utf-8')
    assert first['action'] == 'installed'
    assert second['action'] == 'updated'
    assert text.count(MANAGED_BLOCK_START) == 1
    assert text.startswith('before')


def test_dashboard_skills_live_api_contract() -> None:
    html = Path('prismatic/gateway/templates/dashboard.html').read_text(encoding='utf-8')
    assert 'mockSkills' not in html
    assert 'fetch("/api/skills")' in html
    assert '/api/skills/${encodeURIComponent(skillId)}/${action}' in html
    assert 'skills-grid' in html
    assert 'toggleSkillInstall' in html


def test_agent_context_cli_and_fresh_venv_console_script(tmp_path: Path) -> None:
    env = os.environ.copy()
    doc = tmp_path / 'AGENTS.md'
    line = subprocess.run([sys.executable, '-m', 'prismatic.agent_context', 'line', '--agent', 'hermes'], text=True, capture_output=True, check=True, env=env)
    assert 'Prismatic Skills Registry' in line.stdout
    install = subprocess.run([sys.executable, '-m', 'prismatic.agent_context', 'install-doc', str(doc), '--agent', 'hermes'], text=True, capture_output=True, check=True, env=env)
    assert json.loads(install.stdout)['ok'] is True
    assert MANAGED_BLOCK_START in doc.read_text(encoding='utf-8')

    venv_root = tmp_path / 'venv-smoke'
    subprocess.run([sys.executable, '-m', 'venv', str(venv_root)], check=True)
    subprocess.run([str(venv_root / 'bin' / 'pip'), 'install', '--no-deps', '.'], check=True, stdout=subprocess.DEVNULL)
    console = subprocess.run([str(venv_root / 'bin' / 'prismatic-agent-context'), 'line', '--agent', 'agy'], text=True, capture_output=True, check=True)
    assert 'Prismatic Skills Registry' in console.stdout
