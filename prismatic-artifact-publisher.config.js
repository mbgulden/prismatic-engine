const fs = require('fs');
const path = require('path');
const os = require('os');

const homeDir = os.homedir();
const venvPython = path.join(homeDir, '.prismatic', 'venv_stable', 'bin', 'python3');
const interpreter = fs.existsSync(venvPython) ? venvPython : 'python3';

let prismaticHome = process.env.PRISMATIC_HOME || path.join(homeDir, "work");
if (prismaticHome.includes("agy_sandboxes") || prismaticHome.includes("tmp")) {
  prismaticHome = path.join(homeDir, "work");
}

module.exports = {
  apps: [
    {
      name: "hermes-artifact-publisher",
      script: "./bin/prismatic_artifact_publisher.py",
      interpreter: interpreter,
      cwd: __dirname,
      env: {
        PRISMATIC_ARTIFACT_HOST: "127.0.0.1",
        PRISMATIC_ARTIFACT_PORT: "9120",
        PRISMATIC_HOME: prismaticHome
      },
      autorestart: true,
      watch: false
    }
  ]
};
