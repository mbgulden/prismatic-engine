const fs = require('fs');
const path = require('path');
const os = require('os');

const homeDir = os.homedir();
const venvPython = path.join(homeDir, '.prismatic', 'venv_stable', 'bin', 'python3');
const interpreter = fs.existsSync(venvPython) ? venvPython : 'python3';

module.exports = {
  apps: [
    {
      name: "prismatic-artifact-publisher",
      script: "./bin/prismatic_artifact_publisher.py",
      interpreter: interpreter,
      cwd: __dirname,
      env: {
        PRISMATIC_ARTIFACT_HOST: "127.0.0.1",
        PRISMATIC_ARTIFACT_PORT: "9120",
        PRISMATIC_HOME: process.env.PRISMATIC_HOME || path.join(homeDir, "work")
      },
      autorestart: true,
      watch: false
    }
  ]
};
